"""Verified, journaled Windows dependency updates. No automatic installation."""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SOURCES = {
    "yt-dlp": ("yt-dlp/yt-dlp", "latest", "yt-dlp.exe"),
    "deno": ("denoland/deno", "latest", "deno-x86_64-pc-windows-msvc.zip"),
    "ffmpeg": ("BtbN/FFmpeg-Builds", "tags/latest", "ffmpeg-master-latest-win64-gpl.zip"),
    "douk": ("JoeanAmier/TikTokDownloader", "latest", None),
}
DOUK_HASHES = {
    "5.7": "2e2a6e80f1298cfbb95d6e4d64461feb625853c84c498ede5781d7eb4baa904d",
    "5.8": "da81823ed80d1d38f49f1f7dbe441ec564ab3216e01378a940a91f44bc52d5c0",
}
TARGETS = {"yt-dlp": "yt-dlp.exe", "deno": "deno", "ffmpeg": "ffmpeg", "douk": "douk"}


def read(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8-sig")) if Path(path).exists() else (default if default is not None else {})


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version_tuple(value):
    match = re.search(r"\d+(?:\.\d+)+", value)
    return tuple(int(x) for x in match.group(0).split(".")) if match else ()


def configured_path(value):
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


def javascript_runtime(value):
    kind, separator, executable = str(value).partition(":")
    kind = kind.strip().lower()
    if not separator or not re.fullmatch(r"[a-z][a-z0-9_-]*", kind) or not executable.strip():
        raise ValueError("JavaScript 运行时配置格式无效")
    return kind, configured_path(executable)


def runtime_version(kind, executable):
    executable = Path(executable)
    if not executable.is_file():
        raise ValueError(f"{kind} 可执行文件不存在：{executable}")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run([str(executable), "--version"], capture_output=True, timeout=25, creationflags=flags)
    text = (result.stdout + result.stderr).decode("utf-8", "replace").strip()
    if result.returncode or not text:
        raise ValueError(f"{kind} 版本检测失败")
    first = text.splitlines()[0][:180]
    return first if first.casefold().startswith(kind.casefold()) else f"{kind} {first}"


def release_asset(name, release):
    repo, _, filename = SOURCES[name]
    tag = str(release.get("tag_name", ""))
    if not tag or release.get("draft") or (release.get("prerelease") and name != "ffmpeg"):
        raise ValueError("发布信息无效或不是稳定版")
    if name == "douk":
        filename = f"DouK-Downloader_V{tag.removeprefix('v')}_Windows_X64.zip"
    matches = [a for a in release.get("assets", []) if a.get("name") == filename]
    if len(matches) != 1:
        raise ValueError("官方发布中缺少唯一的 Windows x64 附件")
    asset = matches[0]
    digest = str(asset.get("digest", ""))
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError("官方未提供有效 SHA-256，不能安装")
    url = str(asset.get("browser_download_url", ""))
    expected = f"https://github.com/{repo}/releases/download/{urllib.parse.quote(tag, safe='')}/{filename}"
    if url != expected or not 0 < int(asset.get("size", 0)) < 2 * 1024**3:
        raise ValueError("附件地址、仓库或大小不符合预期")
    return {"name": name, "version": tag, "url": url, "sha256": digest[7:].lower(),
            "size": int(asset["size"]), "asset_id": asset.get("id"),
            "release_url": f"https://github.com/{repo}/releases/tag/{urllib.parse.quote(tag, safe='')}",
            "updated_at": asset.get("updated_at", "")}


def fetch_release(name):
    repo, endpoint, _ = SOURCES[name]
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/{endpoint}",
                                     headers={"User-Agent": "VideoDownloadCompanion/0.1.15", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("发布信息过大")
    return release_asset(name, json.loads(data))


def download(asset, destination, progress=lambda text: None):
    request = urllib.request.Request(asset["url"], headers={"User-Agent": "VideoDownloadCompanion/0.1.15"})
    total = 0
    last = -1
    with urllib.request.urlopen(request, timeout=30) as response, Path(destination).open("wb") as output:
        final = urllib.parse.urlparse(response.url)
        if final.scheme != "https" or final.hostname not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
            raise ValueError("下载被重定向到非官方资产服务")
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            total += len(chunk)
            if total > asset["size"]:
                raise ValueError("下载大小超过官方声明")
            output.write(chunk)
            percent = total * 100 // asset["size"]
            if percent // 10 != last:
                progress(f"{asset['name']}：下载 {percent}%")
                last = percent // 10
    if total != asset["size"] or sha(destination) != asset["sha256"]:
        raise ValueError("附件大小或 SHA-256 校验失败，保留旧版")


def extract(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as source:
        if sum(x.file_size for x in source.infolist()) > 3 * 1024**3:
            raise ValueError("压缩包解压大小超限")
        seen = set()
        for item in source.infolist():
            # Reject Windows alternate streams, traversal, junction-like entries and case collisions.
            parts = item.filename.replace("\\", "/").split("/")
            target = (destination / item.filename).resolve()
            key = str(target).casefold()
            if (not target.is_relative_to(destination) or any(":" in x or x in {"..", "."} for x in parts)
                    or item.filename.startswith(("/", "\\")) or key in seen
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("不安全的压缩包路径")
            seen.add(key)
        source.extractall(destination)


def payload(tx, name, label):
    return tx / (label + (".exe" if name == "yt-dlp" else ""))


def clone(source, destination):
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def assert_idle(name, target):
    """Also reject tools launched outside the download queue (e.g. native DouK)."""
    if os.name != "nt":
        return
    from ctypes import wintypes as w
    class Entry(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD),
                    ("heap", ctypes.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
                    ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD), ("exe", w.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError("无法确认依赖进程已退出")
    entry = Entry()
    entry.size = ctypes.sizeof(Entry)
    watched = [target.resolve()]
    if name == "douk":
        watched.append((target.parent / "douk-5.7-cookie-reader").resolve())
    try:
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            handle = kernel.OpenProcess(0x1000, False, entry.pid)
            if handle:
                try:
                    buffer = ctypes.create_unicode_buffer(32768)
                    length = w.DWORD(len(buffer))
                    if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                        running = Path(buffer.value).resolve()
                        if any(running == path or running.is_relative_to(path) for path in watched):
                            raise RuntimeError(f"{name} 仍在运行，请关闭相关窗口或等待任务结束后再更新")
                finally:
                    kernel.CloseHandle(handle)
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)


def smoke(name, target, launch_douk=False):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    if name == "douk":
        actual = sha(target / "main.exe")
        if actual not in DOUK_HASHES.values():
            raise ValueError("DouK 可执行文件尚未经兼容性验证")
        if launch_douk and ((target / "Volume/settings.json").exists() or (target / "_internal/Volume/settings.json").exists()):
            result = subprocess.run([str(target / "main.exe")], input=b"Q\n", cwd=target,
                                    capture_output=True, timeout=30, creationflags=flags)
            if result.returncode or b"Traceback" in result.stdout + result.stderr:
                raise ValueError("DouK 启动自检失败")
        return next(v for v, h in DOUK_HASHES.items() if h == actual)
    files = [target] if name == "yt-dlp" else ([target / "deno.exe"] if name == "deno" else [target / "ffmpeg.exe", target / "ffprobe.exe"])
    lines = []
    for exe in files:
        result = subprocess.run([str(exe), "-version" if name == "ffmpeg" else "--version"],
                                capture_output=True, timeout=25, creationflags=flags)
        text = result.stdout.decode("utf-8", "replace")
        if result.returncode or not text.strip():
            raise ValueError(f"{exe.name} 启动自检失败")
        lines.append(text.splitlines()[0][:180])
    return " / ".join(lines)


class Updater:
    def __init__(self, app: Path, config: Path, fetch=fetch_release, downloader=download, probe=smoke,
                 runtime_probe=runtime_version):
        self.app, self.config = Path(app).resolve(), Path(config).resolve()
        self.tools = self.app / "tools"
        self.transactions = self.tools / ".updates"
        self.state_path = self.config.parent / "dependency-updates.json"
        self.fetch, self.downloader, self.probe, self.runtime_probe = fetch, downloader, probe, runtime_probe
        self.state = read(self.state_path)

    def configured_target(self, name):
        config = read(self.config)
        if name == "deno":
            kind, executable = javascript_runtime(config["yt_dlp"]["js_runtime"])
            return (executable.parent if kind == "deno" else executable), kind
        configured = {"yt-dlp": config["yt_dlp"]["executable"],
                      "ffmpeg": config["yt_dlp"]["ffmpeg_location"],
                      "douk": config["douk"]["root"]}[name]
        return configured_path(configured), name

    def managed_path(self, name):
        expected = (self.tools / TARGETS[name]).resolve()
        if expected.parent != self.tools.resolve():
            raise ValueError("依赖路径不在应用 tools 目录内")
        return expected

    def managed_target(self, name):
        expected = self.managed_path(name)
        configured, kind = self.configured_target(name)
        if kind != name or configured.resolve() != expected:
            raise ValueError("当前依赖位于自定义路径，请使用“纳管／更新选中项”迁移")
        return expected

    # Keep the original public method name for callers that require a writable, managed target.
    def target(self, name):
        return self.managed_target(name)

    def _config_values(self, name):
        config = read(self.config)
        if name == "yt-dlp":
            return {"executable": config["yt_dlp"]["executable"]}
        if name == "deno":
            return {"js_runtime": config["yt_dlp"]["js_runtime"]}
        if name == "ffmpeg":
            return {"ffmpeg_location": config["yt_dlp"]["ffmpeg_location"]}
        return {"root": config["douk"]["root"],
                "expected_sha256": config["douk"].get("expected_sha256", "")}

    def _set_config_values(self, name, values):
        config = read(self.config)
        section = config["douk"] if name == "douk" else config["yt_dlp"]
        for key, value in values.items():
            section[key] = value
        write(self.config, config)

    def _managed_config_values(self, name, target):
        if name == "yt-dlp":
            return {"executable": str(target)}
        if name == "deno":
            return {"js_runtime": "deno:" + str(target / "deno.exe")}
        if name == "ffmpeg":
            return {"ffmpeg_location": str(target)}
        return {"root": str(target), "expected_sha256": sha(target / "main.exe").upper()}

    def fingerprints(self, name, target):
        if name == "yt-dlp":
            return {"yt-dlp.exe": sha(target)}
        if name == "douk":
            return {str(p.relative_to(target)): sha(p) for p in target.rglob("*")
                    if p.is_file() and "Volume" not in p.relative_to(target).parts}
        files = [target] if name == "yt-dlp" else ([target / "main.exe"] if name == "douk" else
                ([target / "deno.exe"] if name == "deno" else [target / "ffmpeg.exe", target / "ffprobe.exe"]))
        return {p.name: sha(p) for p in files}

    def check(self, force=False):
        self.state = read(self.state_path)
        cache = self.state.get("cache", {})
        use_cache = not force and time.time() - cache.get("time", 0) < 6 * 3600
        def one(name):
            item = {"name": name, "status": "error", "current": "未知"}
            try:
                target, kind = self.configured_target(name)
                if name == "deno" and kind != "deno":
                    item.update(current=self.runtime_probe(kind, target), status="alternate", managed=False,
                                display_name=f"js-runtime ({kind})",
                                runtime=kind, message=f"当前配置使用 {kind}，不适用 Deno 更新")
                    return item
                item["current"] = self.probe(name, target)
                managed = True
                try:
                    self.managed_target(name)
                except ValueError:
                    managed = False
                item["managed"] = managed
                candidate = cache.get("assets", {}).get(name) if use_cache else None
                candidate = candidate or self.fetch(name)
                item["asset"] = candidate
                installed = self.state.get("installed", {}).get(name, {})
                fingerprints = self.fingerprints(name, target) if managed else None
                if managed and installed.get("fingerprints") == fingerprints and installed.get("sha256") == candidate["sha256"]:
                    item["status"] = "current"
                elif name in {"yt-dlp", "deno", "douk"} and version_tuple(item["current"]) >= version_tuple(candidate["version"]):
                    # Deno --version has multiple lines; smoke returns only its first line.
                    item["status"] = "current"
                else:
                    item["status"] = "available"
                if name == "douk" and candidate["version"] != "5.8" and item["status"] == "available":
                    item.update(status="blocked", message="已发现新版，须先验证菜单与数据目录兼容性")
                elif item["status"] == "available" and self.state.get("skipped", {}).get(name) == candidate["sha256"]:
                    item["status"] = "skipped"
                if not managed:
                    item["update_available"] = item["status"] in {"available", "blocked", "skipped"}
                    comparable = name == "ffmpeg" or not version_tuple(candidate["version"]) or not version_tuple(item["current"]) \
                        or version_tuple(item["current"]) <= version_tuple(candidate["version"])
                    managed_path_free = not self.managed_path(name).exists()
                    verified = name != "douk" or candidate["version"] == "5.8"
                    item["can_adopt"] = item["status"] != "blocked" and comparable and managed_path_free and verified
                    detail = item.get("message", "")
                    if not managed_path_free:
                        detail = "应用 tools 中已有同名路径，为避免覆盖，暂不能纳管" + ("；" + detail if detail else "")
                    elif not comparable:
                        detail = "自定义版本比官方候选版本新，暂不允许降级纳管" + ("；" + detail if detail else "")
                    elif not verified:
                        detail = "该 DouK 版本尚未经兼容性验证" + ("；" + detail if detail else "")
                    message = "已按配置路径检测版本；可手动纳管，原路径保持不变" if item["can_adopt"] else "已按配置路径检测版本；当前不能纳管"
                    item.update(status="unmanaged", message=message + ("；" + detail if detail else ""))
            except Exception as exc:
                item["message"] = str(exc)
            return item
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(one, SOURCES))
        self.state["cache"] = {"time": time.time(), "assets": {r["name"]: r["asset"] for r in results if "asset" in r}}
        write(self.state_path, self.state)
        return results

    def skip(self, results):
        for row in results:
            if row["status"] == "available":
                self.state.setdefault("skipped", {})[row["name"]] = row["asset"]["sha256"]
        write(self.state_path, self.state)

    def prepare(self, asset, progress=lambda text: None, adoption=False):
        name = asset["name"]
        source = None
        if adoption:
            source, kind = self.configured_target(name)
            if kind != name:
                raise ValueError(f"当前使用 {kind} 运行时，不能自动切换为 Deno")
            if self.managed_path(name).exists():
                raise ValueError("应用 tools 中已有同名路径，为避免覆盖，不能执行纳管")
            self.probe(name, source)
        else:
            self.target(name)
        # Re-fetch immediately before download; a changed mutable release must be re-confirmed.
        fresh = self.fetch(name)
        if any(fresh[k] != asset[k] for k in ("url", "sha256", "size", "version")):
            raise ValueError("发布附件刚发生变化，请重新检查后更新")
        if name == "douk" and fresh["version"] != "5.8":
            raise ValueError("该 DouK 版本尚未经兼容性验证")
        tx = self.transactions / uuid.uuid4().hex
        tx.mkdir(parents=True)
        archive = tx / "download"
        self.downloader(fresh, archive, progress)
        if archive.stat().st_size != fresh["size"] or sha(archive) != fresh["sha256"]:
            raise ValueError("下载校验失败")
        ready = payload(tx, name, "candidate")
        if name == "yt-dlp":
            shutil.copy2(archive, ready)
        else:
            unpack = tx / "unpacked"
            extract(archive, unpack)
            ready.mkdir()
            names = ["deno.exe"] if name == "deno" else (["ffmpeg.exe", "ffprobe.exe"] if name == "ffmpeg" else ["DouK-Downloader.exe"])
            for filename in names:
                matches = list(unpack.rglob(filename))
                if len(matches) != 1:
                    raise ValueError(f"压缩包缺少唯一的 {filename}")
                if name == "douk":
                    shutil.copytree(matches[0].parent, ready, dirs_exist_ok=True)
                    shutil.copy2(ready / filename, ready / "main.exe")
                else:
                    shutil.copy2(matches[0], ready / filename)
        version = self.probe(name, ready)
        if name in {"yt-dlp", "deno", "douk"} and version_tuple(version) != version_tuple(fresh["version"]):
            raise ValueError("可执行文件版本与官方发布不一致")
        return {"tx": str(tx), "asset": fresh, "adoption": adoption,
                **({"source": str(source)} if source is not None else {})}

    def _douk_data(self, source, destination):
        old_volume = source / ("Volume" if (source / "Volume/settings.json").exists() else "_internal/Volume")
        new_volume = destination / ("Volume" if sha(destination / "main.exe") == DOUK_HASHES["5.8"] else "_internal/Volume")
        if not old_volume.is_dir():
            raise ValueError("原 DouK 数据目录不存在")
        if new_volume.exists():
            # This path is only inside a transaction candidate, never the active installation.
            if not new_volume.resolve().is_relative_to(self.transactions.resolve()):
                raise ValueError("拒绝删除事务目录以外的数据")
            shutil.rmtree(new_volume)
        shutil.copytree(old_volume, new_volume)

    def _config_hash(self, value):
        cfg = read(self.config)
        cfg["douk"]["expected_sha256"] = value
        write(self.config, cfg)

    def commit(self, prepared, quiet=contextlib.nullcontext, reload=lambda: None):
        if prepared.get("adoption"):
            return self._commit_adoption(prepared, quiet, reload)
        asset = prepared["asset"]
        name, tx = asset["name"], Path(prepared["tx"]).resolve()
        if tx.parent != self.transactions.resolve():
            raise ValueError("事务路径无效")
        target, ready, previous = self.target(name), payload(tx, name, "candidate"), payload(tx, name, "previous")
        with quiet():
            assert_idle(name, target)
            self.state = read(self.state_path)
            journal = {"name": name, "phase": "prepared", "asset": asset,
                       "old_record": self.state.get("installed", {}).get(name),
                       "old_config_hash": read(self.config)["douk"].get("expected_sha256", ""),
                       "previous_fingerprints": self.fingerprints(name, target)}
            if name == "douk":
                self._douk_data(target, ready)
                legacy = self.tools / "douk-5.7-cookie-reader"
                if sha(target / "main.exe") == DOUK_HASHES["5.7"] and not legacy.exists():
                    shutil.copytree(target, tx / "legacy-reader")
                    (tx / "legacy-reader").rename(legacy)
            write(tx / "journal.json", journal)
            try:
                target.rename(previous)
                ready.rename(target)
                journal["phase"] = "swapped"
                write(tx / "journal.json", journal)
                self.probe(name, target)
                if name == "douk" and self.probe is smoke:
                    smoke(name, target, launch_douk=True)
                if name == "douk":
                    self._config_hash(sha(target / "main.exe").upper())
                self.state.setdefault("installed", {})[name] = {**asset, "fingerprints": self.fingerprints(name, target), "transaction": str(tx)}
                write(self.state_path, self.state)
                reload()
                journal["phase"] = "completed"
                write(tx / "journal.json", journal)
            except BaseException:
                self._restore(tx, journal)
                reload()
                raise
        return tx

    def _commit_adoption(self, prepared, quiet=contextlib.nullcontext, reload=lambda: None):
        asset = prepared["asset"]
        name, tx = asset["name"], Path(prepared["tx"]).resolve()
        if tx.parent != self.transactions.resolve():
            raise ValueError("事务路径无效")
        source = Path(prepared["source"]).resolve()
        configured, kind = self.configured_target(name)
        if kind != name or configured.resolve() != source:
            raise ValueError("准备更新后依赖配置已变化，请重新检查")
        target, ready, previous = self.managed_path(name), payload(tx, name, "candidate"), payload(tx, name, "previous")
        if target.exists():
            raise ValueError("应用 tools 中已有同名路径，为避免覆盖，不能执行纳管")
        with quiet():
            assert_idle(name, source)
            self.state = read(self.state_path)
            old_values = self._config_values(name)
            previous_version = self.probe(name, source)
            if name == "douk":
                self._douk_data(source, ready)
            clone(source, previous)
            journal = {"kind": "adoption", "name": name, "phase": "prepared", "asset": asset,
                       "old_record": self.state.get("installed", {}).get(name),
                       "previous_record": {"name": name, "version": previous_version, "sha256": ""},
                       "old_config_values": old_values,
                       "previous_fingerprints": self.fingerprints(name, source)}
            write(tx / "journal.json", journal)
            try:
                if name == "douk":
                    legacy = self.tools / "douk-5.7-cookie-reader"
                    if sha(source / "main.exe") == DOUK_HASHES["5.7"] and not legacy.exists():
                        shutil.copytree(source, tx / "legacy-reader")
                        (tx / "legacy-reader").rename(legacy)
                ready.rename(target)
                journal["phase"] = "swapped"
                write(tx / "journal.json", journal)
                self.probe(name, target)
                if name == "douk" and self.probe is smoke:
                    smoke(name, target, launch_douk=True)
                self._set_config_values(name, self._managed_config_values(name, target))
                self.state.setdefault("installed", {})[name] = {
                    **asset, "fingerprints": self.fingerprints(name, target),
                    "transaction": str(tx), "adopted_from": str(source)}
                write(self.state_path, self.state)
                reload()
                journal["phase"] = "completed"
                write(tx / "journal.json", journal)
            except BaseException:
                self._restore_adoption(tx, journal)
                reload()
                raise
        return tx

    def _restore(self, tx, journal):
        name = journal["name"]
        target, previous = self.target(name), payload(tx, name, "previous")
        if previous.exists():
            if target.exists():
                target.rename(tx / ("failed-" + uuid.uuid4().hex))
            previous.rename(target)
        if name == "douk":
            self._config_hash(journal["old_config_hash"])
        self.state = read(self.state_path)
        records = self.state.setdefault("installed", {})
        if journal["old_record"] is None:
            records.pop(name, None)
        else:
            records[name] = journal["old_record"]
        write(self.state_path, self.state)
        journal["phase"] = "recovered"
        write(tx / "journal.json", journal)

    def _restore_adoption(self, tx, journal):
        name = journal["name"]
        target = self.managed_path(name)
        if target.exists():
            target.rename(tx / ("failed-" + uuid.uuid4().hex))
        self._set_config_values(name, journal["old_config_values"])
        self.state = read(self.state_path)
        records = self.state.setdefault("installed", {})
        if journal["old_record"] is None:
            records.pop(name, None)
        else:
            records[name] = journal["old_record"]
        write(self.state_path, self.state)
        journal["phase"] = "recovered"
        write(tx / "journal.json", journal)

    def recover(self):
        recovered = []
        for path in sorted(self.transactions.glob("*/journal.json")):
            if path.parent.resolve().parent != self.transactions.resolve():
                raise ValueError("事务路径无效")
            journal = read(path)
            if journal["phase"] not in {"completed", "recovered"}:
                if journal.get("kind") == "adoption":
                    self._restore_adoption(path.parent, journal)
                else:
                    self._restore(path.parent, journal)
                recovered.append(journal["name"])
        return recovered

    def rollback(self, name, quiet=contextlib.nullcontext, reload=lambda: None):
        self.state = read(self.state_path)
        record = self.state.get("installed", {}).get(name, {})
        old_tx = Path(record.get("transaction", "")).resolve()
        if old_tx.parent != self.transactions.resolve() or not payload(old_tx, name, "previous").exists():
            raise ValueError("该依赖没有可用的更新前备份")
        journal = read(old_tx / "journal.json")
        if self.fingerprints(name, payload(old_tx, name, "previous")) != journal["previous_fingerprints"]:
            raise ValueError("回退文件校验失败")
        tx = self.transactions / uuid.uuid4().hex
        tx.mkdir()
        clone(payload(old_tx, name, "previous"), payload(tx, name, "candidate"))
        previous_record = journal.get("previous_record") or journal["old_record"] or {
            "name": name, "version": self.probe(name, payload(tx, name, "candidate")), "sha256": ""}
        return self.commit({"tx": str(tx), "asset": {**previous_record, "name": name}}, quiet, reload)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(Updater(args.app, args.config).check(force=True), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
