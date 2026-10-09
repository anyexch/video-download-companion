#!/usr/bin/env python3
"""Windows tray companion for the three-platform download scheduler."""

from __future__ import annotations

import ctypes
import contextlib
import json
import os
import secrets
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
import winreg
from pathlib import Path
from tkinter import BooleanVar, Button, Checkbutton, Entry, Frame, Label, StringVar, Tk, filedialog, messagebox, ttk

import pystray
from PIL import Image, ImageDraw

import ytdlp_listener as listener
import dependency_updates as updates


APP_NAME = "Video Download Companion"
APP_VERSION = "0.1.15"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "VideoDownloadCompanion"
MUTEX_NAME = r"Local\VideoDownloadCompanion-3F31663E"
ADMIN_COOKIE_BROWSERS = {"Chrome", "Chromium", "Edge"}


def state_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "YouTubeYtDlpBridge"


def default_config_path() -> Path:
    return state_dir() / "config.json"


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def douk_initialization_status(root: Path) -> dict:
    volume = root.resolve() / "Volume"
    if not (volume / "settings.json").is_file():
        volume = root.resolve() / "_internal" / "Volume"
    database = volume / "DouK-Downloader.db"
    result = {
        "initialized": False,
        "disclaimer_accepted": False,
        "language": "",
        "database": str(database),
    }
    if not database.is_file():
        result["error"] = f"DouK database does not exist: {database}"
        return result
    try:
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            disclaimer = connection.execute(
                "SELECT VALUE FROM config_data WHERE NAME = ?", ("Disclaimer",)
            ).fetchone()
            language = connection.execute(
                "SELECT VALUE FROM option_data WHERE NAME = ?", ("Language",)
            ).fetchone()
        finally:
            connection.close()
        result["disclaimer_accepted"] = bool(disclaimer and str(disclaimer[0]) == "1")
        result["language"] = str(language[0]) if language else ""
        result["initialized"] = bool(
            result["disclaimer_accepted"] and result["language"] in {"zh_CN", "en_US"}
        )
    except Exception as exc:
        result["error"] = str(exc)
    return result


def executable_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    runtime = pythonw if pythonw.is_file() else Path(sys.executable)
    return f'"{runtime}" "{Path(__file__).resolve()}"'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
        return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, executable_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def tray_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, 58, 58), radius=14, fill="#2563eb")
    draw.polygon(((23, 17), (48, 32), (23, 47)), fill="white")
    return image


class ListenerRuntime:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.server = None
        self.manager = None
        self.thread: threading.Thread | None = None
        self.error = ""
        self.ready = threading.Event()

    def start(self) -> bool:
        if self.thread and self.thread.is_alive():
            return True
        self.error = ""
        self.ready.clear()
        self.thread = threading.Thread(target=self._serve, name="companion-listener", daemon=True)
        self.thread.start()
        self.ready.wait(4)
        return self.server is not None and not self.error

    def _serve(self) -> None:
        try:
            config = listener.load_config(self.config_path.resolve())
            logger = listener.configure_logging(Path(config["downloads"]["log_file"]))
            self.manager = listener.DownloadManager(config, logger)
            address = (str(config["server"]["host"]), int(config["server"]["port"]))
            self.server = listener.BridgeServer(address, self.manager, config, logger)
            self.manager.start()
            (self.config_path.parent / "listener.pid").write_text(str(os.getpid()), encoding="ascii")
            logger.info("Companion %s listening on http://%s:%s", APP_VERSION, *address)
            self.ready.set()
            self.server.serve_forever(poll_interval=0.5)
        except BaseException as exc:
            self.error = str(exc)
            try:
                state_dir().mkdir(parents=True, exist_ok=True)
                (state_dir() / "companion-error.log").write_text(traceback.format_exc(), encoding="utf-8")
            except OSError:
                pass
            self.ready.set()
        finally:
            if self.server:
                self.server.server_close()
            if self.manager:
                self.manager.stop()
            self.server = None
            self.manager = None
            try:
                (self.config_path.parent / "listener.pid").unlink(missing_ok=True)
            except OSError:
                pass

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
        if self.thread:
            self.thread.join(timeout=8)
        self.thread = None

    def restart(self) -> bool:
        self.stop()
        return self.start()


class CompanionApp:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.runtime = ListenerRuntime(config_path)
        self.icon = pystray.Icon(APP_NAME, tray_image(), APP_NAME)
        self.settings_lock = threading.Lock()
        self.update_lock = threading.Lock()
        self.update_window_lock = threading.Lock()
        self.cookie_busy = threading.Event()
        self.updater = updates.Updater(application_dir(), config_path)
        self.exiting = False
        self.icon.menu = pystray.Menu(
            pystray.MenuItem(self.status_text, None, enabled=False),
            pystray.MenuItem("打开设置", self.open_settings, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("启动调度器", self.start_listener),
            pystray.MenuItem("停止调度器", self.stop_listener),
            pystray.MenuItem(self.douk_cookie_menu_text, self.refresh_douk_cookie),
            pystray.MenuItem("检查依赖更新／回退…", self.open_updates),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开下载目录", self.open_download_directory),
            pystray.MenuItem("打开日志", self.open_log),
            pystray.MenuItem("登录后自动启动", self.toggle_autostart, checked=lambda _item: autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self.exit),
        )

    def api_status(self) -> dict | None:
        try:
            with urllib.request.urlopen("http://127.0.0.1:17392/api/health", timeout=0.7) as response:
                return json.load(response)
        except Exception:
            return None

    def status_text(self, _item=None) -> str:
        status = self.api_status()
        if not status:
            return "调度器：离线"
        return f"调度器：在线 · 运行 {status.get('active', 0)} · 等待 {status.get('queueDepth', 0)}"

    def notify(self, title: str, message: str) -> None:
        try:
            self.icon.notify(message, title)
        except Exception:
            pass

    def start_listener(self, _icon=None, _item=None) -> None:
        if self.api_status():
            self.notify(APP_NAME, "调度器已经在线。")
            return
        if self.runtime.start():
            self.notify(APP_NAME, "调度器已启动。")
        else:
            self.notify(APP_NAME, f"启动失败：{self.runtime.error}")
        self.icon.update_menu()

    def stop_listener(self, _icon=None, _item=None) -> None:
        if self.update_lock.locked():
            self.notify(APP_NAME, "正在检查或更新依赖，请稍后停止。")
            return
        if self.runtime.server:
            self.runtime.stop()
            self.notify(APP_NAME, "调度器已停止；未完成任务仍保存在 SQLite。")
        elif self.api_status():
            self.notify(APP_NAME, "当前在线的是外部监听进程，请先用旧版停止脚本关闭。")
        else:
            self.notify(APP_NAME, "调度器已经离线。")
        self.icon.update_menu()

    def config(self) -> dict:
        return read_json(self.config_path)

    def open_path(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)  # type: ignore[attr-defined]

    def open_download_directory(self, _icon=None, _item=None) -> None:
        config = self.config()
        self.open_path(Path(os.path.expandvars(config["downloads"]["directory"])))

    def open_log(self, _icon=None, _item=None) -> None:
        log_path = Path(os.path.expandvars(self.config()["downloads"]["log_file"]))
        if log_path.exists():
            os.startfile(log_path)  # type: ignore[attr-defined]
        else:
            self.notify(APP_NAME, "日志尚未生成。")

    def toggle_autostart(self, _icon=None, _item=None) -> None:
        set_autostart(not autostart_enabled())
        self.icon.update_menu()

    def douk_cookie_menu_text(self, _item=None) -> str:
        browser = str(self.config()["douk"].get("browser", "Firefox"))
        permission = "管理员" if browser in ADMIN_COOKIE_BROWSERS else "无需管理员"
        return f"调用 DouK 从 {browser} 刷新 Cookie（{permission}）"

    def refresh_douk_cookie(self, _icon=None, _item=None) -> None:
        if not self.update_lock.acquire(blocking=False):
            self.notify(APP_NAME, "依赖更新或 Cookie 刷新正在进行，请稍后重试。")
            return
        try:
            if self.cookie_busy.is_set():
                self.notify(APP_NAME, "Cookie 刷新正在进行，请稍后重试。")
                return
            self._refresh_douk_cookie()
        finally:
            self.update_lock.release()

    def _refresh_douk_cookie(self) -> None:
        config = self.config()
        douk = config["douk"]
        browser = str(douk.get("browser", "Firefox"))
        root = Path(os.path.expandvars(douk["root"])).resolve()
        if not (root / "main.exe").is_file():
            self.notify(APP_NAME, f"DouK main.exe 不存在：{root / 'main.exe'}")
            return
        wrapper_source = Path(douk["wrapper"]).resolve().with_name("refresh_douk_cookie.ps1")
        if not wrapper_source.is_file():
            self.notify(APP_NAME, f"DouK 命令包装脚本不存在：{wrapper_source}")
            return
        wrapper = state_dir() / "refresh_douk_cookie.run.ps1"
        wrapper.write_text(wrapper_source.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
        result_file = state_dir() / "douk-cookie-refresh-result.json"
        result_file.unlink(missing_ok=True)
        arguments = subprocess.list2cmdline([
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper),
            "-DouKRoot", str(root),
            "-Browser", browser,
            "-ExpectedSha256", str(douk.get("expected_sha256", "")),
            "-ResultFile", str(result_file),
        ])
        verb = "runas" if browser in ADMIN_COOKIE_BROWSERS else "open"
        result = ctypes.windll.shell32.ShellExecuteW(None, verb, "powershell.exe", arguments, str(root), 0)
        if result <= 32:
            self.notify(APP_NAME, "未能启动 DouK Cookie 刷新命令。")
            return
        self.notify(APP_NAME, f"正在调用 DouK 从 {browser} 读取 Cookie。")
        self.cookie_busy.set()
        threading.Thread(target=self._watch_cookie_result, args=(result_file,), daemon=True).start()

    def _watch_cookie_result(self, result_file: Path) -> None:
        for _ in range(180):
            if result_file.is_file():
                self.cookie_busy.clear()
                try:
                    result = read_json(result_file)
                    self.notify("DouK Cookie", str(result.get("message", result.get("status", "已结束"))))
                except Exception as exc:
                    self.notify("DouK Cookie", f"无法读取刷新结果：{exc}")
                return
            time.sleep(1)
        self.notify("DouK Cookie", "刷新进程长时间未返回，请查看 DouK 窗口或日志。")
        # Keep updates blocked until the helper has demonstrably exited.

    def open_settings(self, _icon=None, _item=None) -> None:
        if self.update_lock.locked():
            self.notify(APP_NAME, "依赖操作正在进行，请稍后打开设置。")
            return
        if not self.settings_lock.acquire(blocking=False):
            return
        threading.Thread(target=self._settings_window, daemon=True).start()

    @contextlib.contextmanager
    def _quiet_updates(self):
        if self.cookie_busy.is_set():
            raise RuntimeError("Cookie 刷新尚未结束，请稍后更新。")
        manager = self.runtime.manager
        if manager is None and self.api_status():
            raise RuntimeError("另一个下载服务正在运行，请先退出该服务。")
        if manager:
            manager.pause_and_wait(timeout=120)
        try:
            yield
        finally:
            if manager:
                manager.resume_dispatch()

    def _reload_dependency_config(self):
        if self.runtime.manager:
            with self.runtime.manager.lock:
                self.runtime.manager.config = listener.load_config(self.config_path)

    def _startup_check(self):
        # A failed check must never block startup.
        if not self.update_lock.acquire(blocking=False):
            return
        try:
            rows = self.updater.check()
            notices = [r for r in rows if r["status"] in {"available", "blocked"}]
            signature = "|".join(r["name"] + ":" + r["asset"]["sha256"] for r in notices)
            if signature and (signature != self.updater.state.get("notified") or time.time() - self.updater.state.get("notified_at", 0) > 86400):
                self.notify("发现依赖更新", "、".join(r["name"] for r in notices) + "：右键托盘 → 检查依赖更新／回退。")
                self.updater.state["notified"] = signature
                self.updater.state["notified_at"] = time.time()
                updates.write(self.updater.state_path, self.updater.state)
        except Exception as exc:
            updates.write(self.config_path.parent / "dependency-check-error.json", {"message": str(exc), "time": time.time()})
        finally:
            self.update_lock.release()

    def open_updates(self, _icon=None, _item=None):
        if not self.update_window_lock.acquire(blocking=False):
            return
        threading.Thread(target=self._updates_window, daemon=True).start()

    def _updates_window(self):
        root = Tk()
        root.title(f"依赖更新与回退 · {APP_VERSION}")
        root.geometry("960x460")
        root.minsize(860, 420)
        Label(root, text="启动时自动检查；点击后才下载安装。自定义路径可手动纳管，原目录保持不变。", anchor="w").pack(fill="x", padx=18, pady=12)
        tree = ttk.Treeview(root, columns=("current", "latest", "status"), height=6)
        tree.heading("#0", text="组件")
        tree.column("#0", width=95, stretch=False)
        for key, label, width in [("current", "当前版本", 390), ("latest", "可用版本", 120), ("status", "状态", 240)]:
            tree.heading(key, text=label)
            tree.column(key, width=width)
        tree.pack(fill="both", expand=True, padx=18)
        status = StringVar(value="正在检查官方发布…")
        Label(root, textvariable=status, anchor="w", justify="left", wraplength=910).pack(fill="x", padx=18, pady=12)
        Label(root, text="DouK 未验证的新版本仅提示；FFmpeg 会同时更新 ffmpeg 与 ffprobe。", fg="#475569", anchor="w").pack(fill="x", padx=18)
        bar = Frame(root)
        bar.pack(fill="x", padx=18, pady=14)
        events = queue.Queue()
        rows = []
        busy = False
        labels = {"current": "已是最新／本地版本较新", "available": "可以更新", "blocked": "待兼容验证",
                  "skipped": "已跳过（可手动更新）", "unmanaged": "自定义路径",
                  "alternate": "使用其他运行时", "error": "检查失败"}
        buttons = []

        def worker(action, selected):
            if not self.update_lock.acquire(blocking=False):
                events.put(("error", "另一个检查或更新正在进行，请稍后重试。"))
                return
            try:
                if action == "check":
                    result = self.updater.check(force=True)
                elif action == "skip":
                    self.updater.skip(rows)
                    result = self.updater.check()
                else:
                    if self.settings_lock.locked() or self.cookie_busy.is_set():
                        raise RuntimeError("请先关闭设置或等待 Cookie 刷新完成。")
                    if action == "rollback":
                        if not selected:
                            raise RuntimeError("请先选中一个要回退的组件。")
                        events.put(("progress", "等待下载完成并恢复选中组件的上一版…"))
                        self.updater.rollback(selected, self._quiet_updates, self._reload_dependency_config)
                    elif action == "adopt":
                        row = next((value for value in rows if value["name"] == selected), None)
                        if not row or row.get("status") != "unmanaged" or not row.get("can_adopt"):
                            raise RuntimeError("当前组件不能纳管；请重新检查并查看状态说明。")
                        prepared = self.updater.prepare(
                            row["asset"], lambda msg: events.put(("progress", msg)), adoption=True)
                        events.put(("progress", row["name"] + "：校验通过，等待在途下载完成后纳管并切换配置…"))
                        self.updater.commit(prepared, self._quiet_updates, self._reload_dependency_config)
                    else:
                        candidates = [r for r in rows if (r["status"] == "available" or (selected and r["status"] == "skipped")) and (not selected or r["name"] == selected)]
                        if not candidates:
                            raise RuntimeError("没有可安装的更新；未验证的 DouK 新版暂不能替换。")
                        for row in candidates:
                            prepared = self.updater.prepare(row["asset"], lambda msg: events.put(("progress", msg)))
                            events.put(("progress", row["name"] + "：校验通过，等待在途下载完成后备份替换…"))
                            self.updater.commit(prepared, self._quiet_updates, self._reload_dependency_config)
                    result = self.updater.check(force=True)
                events.put(("rows", result))
            except Exception as exc:
                events.put(("error", str(exc)))
            finally:
                self.update_lock.release()

        def run(action, selected=""):
            nonlocal busy
            if busy:
                return
            busy = True
            for button in buttons:
                button.configure(state="disabled")
            status.set("正在处理，请稍候。下载期间仍可接收任务，替换时会等待已有下载结束。")
            threading.Thread(target=worker, args=(action, selected), daemon=True).start()

        def selected():
            return tree.selection()[0] if tree.selection() else ""

        def update_selected():
            key = selected()
            if not key:
                status.set("请先选中组件。")
                return
            row = next((value for value in rows if value["name"] == key), {})
            if row.get("status") == "alternate":
                status.set(f"当前使用 {row.get('runtime', '其他')} 运行时，不能自动切换为 Deno。")
                return
            if row.get("status") == "unmanaged":
                if not row.get("can_adopt"):
                    status.set(row.get("message", "当前组件不能纳管。"))
                    return
                if not messagebox.askyesno(
                        "确认纳管依赖",
                        f"将下载并校验官方 {row.get('asset', {}).get('version', '最新版')}，安装到当前 Companion 的 tools 目录。\n\n"
                        "验证成功后才会切换配置；原自定义安装路径不会被修改或删除，并会另存一份回退副本。\n\n"
                        "是否继续？",
                        parent=root):
                    status.set("已取消纳管，未做任何更改。")
                    return
                run("adopt", key)
                return
            run("update", key)

        def release_page():
            key = selected()
            if key:
                row = next((value for value in rows if value["name"] == key), {})
                if row.get("status") == "alternate":
                    status.set(f"当前使用 {row.get('runtime', '其他')} 运行时，Deno 发布页不适用。")
                    return
                webbrowser.open("https://github.com/" + updates.SOURCES[key][0] + "/releases")

        for title, command in [("重新检查", lambda: run("check")), ("纳管／更新选中项", update_selected),
                               ("更新全部可用项", lambda: run("update")), ("跳过这些版本", lambda: run("skip")),
                               ("回退选中项", lambda: run("rollback", selected())), ("项目发布页", release_page)]:
            button = Button(bar, text=title, command=command)
            button.pack(side="left", padx=(0, 8))
            buttons.append(button)

        def close():
            if busy:
                status.set("操作进行中，请结束后关闭此窗口。")
            else:
                root.destroy()

        Button(bar, text="稍后／关闭", command=close).pack(side="right")
        root.protocol("WM_DELETE_WINDOW", close)

        def poll():
            nonlocal rows, busy
            while not events.empty():
                kind, value = events.get_nowait()
                if kind == "progress":
                    status.set(value)
                    continue
                busy = False
                for button in buttons:
                    button.configure(state="normal")
                if kind == "error":
                    status.set("未完成：" + value)
                else:
                    rows = value
                    for item in tree.get_children():
                        tree.delete(item)
                    for row in rows:
                        asset = row.get("asset", {})
                        latest = asset.get("version", "—")
                        if latest == "latest":
                            latest = asset.get("updated_at", "latest")[:10]
                        display_status = "可纳管／更新" if row["status"] == "unmanaged" and row.get("can_adopt") else labels[row["status"]]
                        tree.insert("", "end", iid=row["name"], text=row.get("display_name", row["name"]), values=(row["current"].split(" Copyright")[0], latest, display_status))
                    errors = [r["name"] + "：" + r.get("message", "") for r in rows if r["status"] in {"error", "blocked"}]
                    status.set("；".join(errors) if errors else "检查完成。自定义路径可选中后纳管；批量更新不会自动纳管。备份保存在 tools/.updates。")
            root.after(100, poll)

        try:
            run("check")
            root.after(100, poll)
            root.mainloop()
        finally:
            self.update_window_lock.release()

    def _settings_window(self) -> None:
        root = Tk()
        root.title(f"{APP_NAME} {APP_VERSION}")
        root.geometry("690x420")
        root.resizable(False, False)
        config = self.config()
        yt_dir = StringVar(value=config["downloads"]["directory"])
        douk_dir = StringVar(value=config["douk"].get("output_directory", ""))
        ytdlp = config["yt_dlp"]
        platforms = ytdlp.get("platforms", {})
        legacy_proxy = str(ytdlp.get("proxy", ""))
        youtube_proxy = StringVar(value=str(platforms.get("youtube", {}).get("proxy", legacy_proxy) or ""))
        bilibili_proxy = StringVar(value=str(platforms.get("bilibili", {}).get("proxy", "") or ""))
        auto = BooleanVar(value=autostart_enabled())

        def row(label: str, variable: StringVar, number: int, browse: bool = False) -> None:
            Label(root, text=label, anchor="w").place(x=24, y=28 + number * 58, width=180, height=24)
            Entry(root, textvariable=variable).place(x=205, y=28 + number * 58, width=390, height=28)
            if browse:
                Button(root, text="选择…", command=lambda: variable.set(filedialog.askdirectory(initialdir=variable.get()) or variable.get())).place(x=605, y=28 + number * 58, width=62, height=28)

        row("YouTube／Bilibili 目录", yt_dir, 0, True)
        row("Douyin 最终目录", douk_dir, 1, True)
        row("YouTube 代理（留空直连）", youtube_proxy, 2)
        row("Bilibili 代理（建议留空）", bilibili_proxy, 3)
        Checkbutton(root, text="登录 Windows 后自动启动 Companion", variable=auto).place(x=205, y=260, width=300, height=28)
        Label(root, text="两个平台分别决定是否使用代理；DouK Cookie 仍由托盘菜单手动刷新。", fg="#475569").place(x=205, y=295, width=450, height=24)

        def save() -> None:
            try:
                if self.update_lock.locked():
                    raise RuntimeError("依赖操作正在进行，请稍后保存设置。")
                yt = Path(os.path.expandvars(yt_dir.get().strip())).resolve()
                douyin = Path(os.path.expandvars(douk_dir.get().strip())).resolve()
                yt.mkdir(parents=True, exist_ok=True)
                douyin.mkdir(parents=True, exist_ok=True)
                updated = self.config()
                updated["downloads"]["directory"] = str(yt)
                updated["douk"]["output_directory"] = str(douyin)
                updated["douk"]["refresh_cookie_from_browser"] = False
                updated_ytdlp = updated.setdefault("yt_dlp", {})
                updated_platforms = updated_ytdlp.setdefault("platforms", {})
                updated_platforms.setdefault("youtube", {})["proxy"] = youtube_proxy.get().strip()
                updated_platforms.setdefault("bilibili", {})["proxy"] = bilibili_proxy.get().strip()
                updated_ytdlp["proxy"] = ""
                write_json_atomic(self.config_path, updated)
                set_autostart(auto.get())
                if self.runtime.server:
                    self.runtime.restart()
                messagebox.showinfo(APP_NAME, "设置已保存。")
                root.destroy()
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))

        Button(root, text="保存并应用", command=save, bg="#2563eb", fg="white").place(x=475, y=350, width=120, height=34)
        Button(root, text="取消", command=root.destroy).place(x=605, y=350, width=62, height=34)

        def close() -> None:
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", close)
        try:
            root.mainloop()
        finally:
            self.settings_lock.release()

    def exit(self, _icon=None, _item=None) -> None:
        if self.update_lock.locked():
            self.notify(APP_NAME, "依赖操作正在进行，请结束后退出。")
            return
        self.exiting = True
        self.runtime.stop()
        self.icon.stop()

    def run(self) -> None:
        if not self.config_path.is_file():
            ctypes.windll.user32.MessageBoxW(None, f"配置文件不存在：\n{self.config_path}", APP_NAME, 0x10)
            return
        try:
            self.updater.recover()
        except Exception as exc:
            ctypes.windll.user32.MessageBoxW(None, f"依赖恢复未完成，暂不启动下载：\n{exc}", APP_NAME, 0x10)
            return
        self.runtime.start()
        threading.Thread(target=self._startup_check, daemon=True).start()
        self.icon.run()


def main() -> int:
    if "--version" in sys.argv:
        return 0
    if "--check-dependencies" in sys.argv:
        config_path = Path(sys.argv[sys.argv.index("--config") + 1]) if "--config" in sys.argv else default_config_path()
        result = updates.Updater(application_dir(), config_path).check(force=True)
        write_json_atomic(Path(sys.argv[sys.argv.index("--result") + 1]), {"version": APP_VERSION, "dependencies": result})
        return 0 if all(r["status"] != "error" for r in result) else 2
    if "--douk-init-status" in sys.argv:
        try:
            root = Path(sys.argv[sys.argv.index("--douk-root") + 1])
            result_file = Path(sys.argv[sys.argv.index("--result") + 1])
        except (ValueError, IndexError):
            return 2
        status = douk_initialization_status(root)
        write_json_atomic(result_file, status)
        return 0 if status["initialized"] else 3

    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not mutex or ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(None, "Video Download Companion 已经在运行。", APP_NAME, 0x40)
        return 0
    try:
        CompanionApp(default_config_path()).run()
        return 0
    finally:
        ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
