#!/usr/bin/env python3
"""Loopback-only download queue for YouTube, Bilibili, and Douyin."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import fnmatch
import hmac
import ipaddress
import json
import logging
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

APP_NAME = "youtube-ytdlp-bridge"
VERSION = "0.7.0"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
BILIBILI_ID_RE = re.compile(r"^(BV[0-9A-Za-z]{10}|av[0-9]+)$")
BILIBILI_BANGUMI_ID_RE = re.compile(r"^(ep|ss)[0-9]+$")
DOUYIN_ID_RE = re.compile(r"^\d{15,22}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def expand_path(value: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(value))).resolve()


def default_config_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".config")
    return Path(base) / "YouTubeYtDlpBridge" / "config.json"


def launch_output_path(path: Path, reveal: bool = False) -> None:
    """Open a completed output or reveal it in the platform file manager."""
    if os.name == "nt":
        if reveal:
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                ["explorer.exe", "/select,", str(path)],
                creationflags=creation_flags,
            )
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]
        return

    command = ["open", str(path.parent if reveal else path)] if sys.platform == "darwin" else [
        "xdg-open",
        str(path.parent if reveal else path),
    ]
    subprocess.Popen(command)


def extract_video_id(raw_url: str) -> str | None:
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_HOSTS:
        return None

    candidate = ""
    parts = [part for part in parsed.path.split("/") if part]
    if host == "youtu.be":
        candidate = parts[0] if parts else ""
    elif parsed.path == "/watch":
        candidate = parse_qs(parsed.query).get("v", [""])[0]
    elif parts and parts[0] in {"shorts", "live", "embed"}:
        candidate = parts[1] if len(parts) > 1 else ""
    return candidate if VIDEO_ID_RE.fullmatch(candidate) else None


def canonical_url(raw_url: str) -> tuple[str, str]:
    video_id = extract_video_id(raw_url)
    if not video_id:
        raise ValueError("URL is not a supported YouTube video URL")
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


def is_bilibili_host(host: str) -> bool:
    return host == "bilibili.com" or host.endswith(".bilibili.com")


def is_douyin_host(host: str) -> bool:
    return host == "douyin.com" or host.endswith(".douyin.com")


def normalize_media_url(raw_url: str) -> tuple[str, str, str]:
    video_id = extract_video_id(raw_url)
    if video_id:
        return "youtube", video_id, f"https://www.youtube.com/watch?v={video_id}"

    try:
        parsed = urlparse(raw_url)
    except ValueError as exc:
        raise ValueError("URL is not a supported YouTube or Bilibili video URL") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL is not a supported media URL")

    parts = [part for part in parsed.path.split("/") if part]
    if is_douyin_host(host):
        if len(parts) >= 2 and parts[0] in {"video", "note"} and DOUYIN_ID_RE.fullmatch(parts[1]):
            content_id = parts[1]
            return "douyin", content_id, f"https://www.douyin.com/{parts[0]}/{content_id}"
        raise ValueError("URL is not a supported Douyin video or note URL")

    if not is_bilibili_host(host):
        raise ValueError("URL is not a supported media URL")

    content_id = ""
    canonical_path = ""
    if len(parts) >= 2 and parts[0] == "video" and BILIBILI_ID_RE.fullmatch(parts[1]):
        content_id = parts[1]
        canonical_path = f"/video/{content_id}"
    elif (
        len(parts) >= 3
        and parts[0] == "bangumi"
        and parts[1] == "play"
        and BILIBILI_BANGUMI_ID_RE.fullmatch(parts[2])
    ):
        content_id = parts[2]
        canonical_path = f"/bangumi/play/{content_id}"
    else:
        raise ValueError("URL is not a supported YouTube or Bilibili video URL")

    page = parse_qs(parsed.query).get("p", [""])[0]
    page_suffix = f"?p={page}" if re.fullmatch(r"[1-9][0-9]*", page) else ""
    return "bilibili", content_id, f"https://www.bilibili.com{canonical_path}{page_suffix}"


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Config not found: {path}. Run setup.ps1 first.") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc

    for section in ("server", "downloads", "yt_dlp", "douk"):
        if not isinstance(config.get(section), dict):
            raise SystemExit(f"Missing config object: {section}")

    token = str(config["server"].get("auth_token", ""))
    if len(token) < 24 or token.startswith("CHANGE_ME"):
        raise SystemExit("server.auth_token must be a random token of at least 24 characters")

    host = str(config["server"].get("host", "127.0.0.1"))
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise SystemExit("Refusing non-loopback server.host; use 127.0.0.1 or ::1")
    except ValueError as exc:
        raise SystemExit("server.host must be a loopback IP address") from exc

    executable = expand_path(str(config["yt_dlp"].get("executable", "")))
    if not executable.is_file():
        raise SystemExit(f"yt-dlp executable not found: {executable}")
    config["yt_dlp"]["executable"] = str(executable)

    ffmpeg_value = str(config["yt_dlp"].get("ffmpeg_location", "")).strip()
    if ffmpeg_value:
        ffmpeg_path = expand_path(ffmpeg_value)
        if not ffmpeg_path.exists():
            raise SystemExit(f"FFmpeg location not found: {ffmpeg_path}")
        config["yt_dlp"]["ffmpeg_location"] = str(ffmpeg_path)

    douk_root = expand_path(str(config["douk"].get("root", "")))
    douk_executable = douk_root / "main.exe"
    if not douk_executable.is_file():
        raise SystemExit(f"DouK executable not found: {douk_executable}")
    douk_wrapper = expand_path(str(config["douk"].get("wrapper", "")))
    if not douk_wrapper.is_file():
        raise SystemExit(f"DouK wrapper not found: {douk_wrapper}")
    config["douk"]["root"] = str(douk_root)
    config["douk"]["wrapper"] = str(douk_wrapper)
    douk_output = str(config["douk"].get("output_directory", "")).strip()
    config["douk"]["output_directory"] = str(expand_path(douk_output)) if douk_output else ""

    scheduler = config.setdefault("scheduler", {})
    scheduler.setdefault("max_total_concurrent", 3)
    scheduler.setdefault("max_ytdlp_concurrent", 2)
    scheduler.setdefault("max_douk_concurrent", 1)
    scheduler.setdefault("max_attempts", 2)
    scheduler.setdefault("retry_delay_seconds", 3)
    for key in ("max_total_concurrent", "max_ytdlp_concurrent", "max_douk_concurrent", "max_attempts"):
        value = int(scheduler[key])
        if value < 1 or value > 16:
            raise SystemExit(f"scheduler.{key} must be between 1 and 16")
        scheduler[key] = value
    scheduler["retry_delay_seconds"] = max(0, min(3600, int(scheduler["retry_delay_seconds"])))

    for key in ("directory", "archive_file", "jobs_file", "log_file"):
        config["downloads"][key] = str(expand_path(str(config["downloads"][key])))
    database_value = str(config["downloads"].get("database_file", "")).strip()
    if not database_value:
        database_value = str(Path(config["downloads"]["jobs_file"]).with_suffix(".db"))
    config["downloads"]["database_file"] = str(expand_path(database_value))
    return config


@dataclass
class Job:
    id: str
    video_id: str
    url: str
    title: str
    source_url: str
    captured_at: str
    platform: str = "youtube"
    created_at: str = field(default_factory=utc_now)
    started_at: str = ""
    finished_at: str = ""
    status: str = "queued"
    exit_code: int | None = None
    output_path: str = ""
    output_paths: list[str] = field(default_factory=list)
    error: str = ""
    updated_at: str = field(default_factory=utc_now)
    progress_percent: float | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bytes_per_second: float | None = None
    eta_seconds: int | None = None
    stage: str = "queued"
    attempts: int = 0
    max_attempts: int = 2


class JobStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                dedupe_key TEXT NOT NULL,
                platform TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)")
        self.connection.commit()

    @staticmethod
    def dedupe_key(job: Job) -> str:
        return f"{job.platform}:{job.video_id}:{urlparse(job.url).query}"

    def save(self, job: Job) -> None:
        payload = json.dumps(asdict(job), ensure_ascii=False, separators=(",", ":"))
        self.connection.execute(
            """
            INSERT INTO jobs(id, dedupe_key, platform, status, created_at, updated_at, payload_json)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                dedupe_key=excluded.dedupe_key,
                platform=excluded.platform,
                status=excluded.status,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                job.id,
                self.dedupe_key(job),
                job.platform,
                job.status,
                job.created_at,
                job.updated_at,
                payload,
            ),
        )
        self.connection.commit()

    def load_for_startup(self, history_limit: int = 1000) -> list[dict[str, Any]]:
        # Always recover every unfinished job, even when the database contains
        # more history than the in-memory/UI window. Recent terminal jobs are
        # loaded only for display and fast duplicate feedback.
        rows = self.connection.execute(
            """
            SELECT payload_json
            FROM jobs
            WHERE status IN ('queued', 'running', 'retrying')
               OR id IN (
                   SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?
               )
            ORDER BY created_at ASC
            """,
            (history_limit,),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])

    def close(self) -> None:
        self.connection.close()


class DownloadManager:
    def __init__(self, config: dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.jobs_path = Path(config["downloads"]["jobs_file"])
        self.store = JobStore(Path(config["downloads"]["database_file"]))
        self.jobs: dict[str, Job] = {}
        self.video_jobs: dict[str, str] = {}
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.pending: list[str] = []
        self.active: dict[str, str] = {}
        self.stopping = False
        self.dispatch_paused = False
        scheduler = config["scheduler"]
        self.max_total = int(scheduler["max_total_concurrent"])
        self.backend_limits = {
            "ytdlp": int(scheduler["max_ytdlp_concurrent"]),
            "douk": int(scheduler["max_douk_concurrent"]),
        }
        self.default_max_attempts = int(scheduler["max_attempts"])
        self.retry_delay_seconds = int(scheduler["retry_delay_seconds"])
        self.executor = ThreadPoolExecutor(max_workers=self.max_total, thread_name_prefix="download-worker")
        self.dispatcher = threading.Thread(target=self._dispatcher_loop, name="download-dispatcher", daemon=True)
        self.last_progress_persist: dict[str, float] = {}
        self._load_jobs()

    def start(self) -> None:
        self.dispatcher.start()

    def pause_and_wait(self, timeout: float = 120) -> None:
        """Keep accepting durable jobs, but drain workers before changing tools."""
        deadline = time.monotonic() + timeout
        with self.condition:
            self.dispatch_paused = True
            while self.active:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self.stopping:
                    self.dispatch_paused = False
                    self.condition.notify_all()
                    raise TimeoutError("下载仍在运行，已取消替换；请下载结束后重试。")
                self.condition.wait(timeout=min(remaining, 1))

    def resume_dispatch(self) -> None:
        with self.condition:
            self.dispatch_paused = False
            self.condition.notify_all()

    def stop(self) -> None:
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        self.dispatcher.join(timeout=5)
        self.executor.shutdown(wait=False, cancel_futures=False)

    def enqueue(self, payload: dict[str, Any]) -> tuple[Job, bool]:
        platform, video_id, url = normalize_media_url(str(payload.get("url", "")))
        dedupe_key = f"{platform}:{video_id}:{urlparse(url).query}"
        with self.lock:
            previous_id = self.video_jobs.get(dedupe_key)
            if previous_id and previous_id in self.jobs:
                previous = self.jobs[previous_id]
                if previous.status in {"queued", "running", "completed"}:
                    return previous, True

            job = Job(
                id=uuid.uuid4().hex,
                video_id=video_id,
                url=url,
                title=str(payload.get("title", ""))[:500],
                source_url=str(payload.get("sourceUrl", ""))[:2000],
                captured_at=str(payload.get("capturedAt", ""))[:100],
                platform=platform,
                max_attempts=self.default_max_attempts,
            )
            # Durability boundary: persist and commit before acknowledging the
            # request or exposing the job to the in-memory scheduler.
            self.store.save(job)
            self.jobs[job.id] = job
            self.video_jobs[dedupe_key] = job.id
            self.pending.append(job.id)
            self.condition.notify_all()
            self.logger.info("Queued job=%s video_id=%s", job.id, video_id)
            return job, False

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def queue_depth(self) -> int:
        with self.lock:
            return len(self.pending)

    def list_jobs(self, limit: int = 50) -> list[Job]:
        with self.lock:
            return sorted(self.jobs.values(), key=lambda item: item.created_at, reverse=True)[:limit]

    def summary(self) -> dict[str, Any]:
        with self.lock:
            counts = {status: 0 for status in ("queued", "running", "retrying", "completed", "failed")}
            for job in self.jobs.values():
                if job.status in counts:
                    counts[job.status] += 1
            active_by_backend = {
                backend: sum(1 for value in self.active.values() if value == backend)
                for backend in self.backend_limits
            }
            return {
                "counts": counts,
                "queueDepth": len(self.pending),
                "active": len(self.active),
                "activeByBackend": active_by_backend,
                "limits": {
                    "total": self.max_total,
                    "ytdlp": self.backend_limits["ytdlp"],
                    "douk": self.backend_limits["douk"],
                },
                "database": str(self.store.path),
            }

    def retry(self, job_id: str) -> Job | None:
        with self.condition:
            job = self.jobs.get(job_id)
            if not job or job.status not in {"failed"}:
                return None
            job.status = "queued"
            job.stage = "queued"
            job.finished_at = ""
            job.exit_code = None
            job.error = ""
            job.progress_percent = None
            job.downloaded_bytes = None
            job.total_bytes = None
            job.speed_bytes_per_second = None
            job.eta_seconds = None
            job.attempts = 0
            job.updated_at = utc_now()
            self.store.save(job)
            if job.id not in self.pending:
                self.pending.append(job.id)
            self.condition.notify_all()
            return job

    def launch_output(self, job_id: str, reveal: bool = False) -> Path:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.status != "completed":
                raise ValueError("Job is not completed")
            recorded_paths = [path for path in (job.output_paths or []) if path]
            if job.output_path and job.output_path not in recorded_paths:
                recorded_paths.insert(0, job.output_path)

        if not recorded_paths:
            raise ValueError("Completed job has no recorded output file")

        missing_path = ""
        for raw_path in recorded_paths:
            candidate = Path(os.path.expandvars(raw_path)).expanduser()
            try:
                resolved = candidate.resolve(strict=True)
            except FileNotFoundError:
                missing_path = str(candidate)
                continue
            if not resolved.is_file():
                missing_path = str(resolved)
                continue
            launch_output_path(resolved, reveal=reveal)
            action = "reveal" if reveal else "open"
            self.logger.info("Output action=%s job=%s path=%s", action, job_id, resolved)
            return resolved

        raise FileNotFoundError(missing_path or recorded_paths[0])

    def _load_jobs(self) -> None:
        try:
            if self.store.count() == 0 and self.jobs_path.is_file():
                raw_jobs = json.loads(self.jobs_path.read_text(encoding="utf-8"))
                for item in raw_jobs:
                    fields = Job.__dataclass_fields__
                    job = Job(**{key: value for key, value in item.items() if key in fields})
                    job.updated_at = job.updated_at or utc_now()
                    job.max_attempts = job.max_attempts or self.default_max_attempts
                    self.store.save(job)
                self.logger.info("Imported %s legacy jobs from %s", len(raw_jobs), self.jobs_path)

            raw_jobs = self.store.load_for_startup()
            for item in raw_jobs:
                fields = Job.__dataclass_fields__
                job = Job(**{key: value for key, value in item.items() if key in fields})
                if job.status in {"queued", "running", "retrying"}:
                    job.status = "queued"
                    job.stage = "resuming"
                    job.error = "Recovered after scheduler restart"
                    job.finished_at = ""
                    job.updated_at = utc_now()
                    self.store.save(job)
                    self.pending.append(job.id)
                self.jobs[job.id] = job
                dedupe_key = f"{job.platform}:{job.video_id}:{urlparse(job.url).query}"
                self.video_jobs[dedupe_key] = job.id
        except (OSError, ValueError, TypeError) as exc:
            self.logger.warning("Could not load job history: %s", exc)

    def _persist_job(self, job: Job) -> None:
        job.updated_at = utc_now()
        self.store.save(job)

    def _build_command(
        self,
        job: Job,
        cookie_override: str | None = None,
        override_cookie: bool = False,
    ) -> list[str]:
        if job.platform == "douyin":
            douk = self.config["douk"]
            command = [
                str(douk.get("powershell_executable", "powershell.exe")),
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", str(douk["wrapper"]),
                "-Urls", job.url,
                "-DouKRoot", str(douk["root"]),
            ]
            if douk.get("refresh_cookie_from_browser", True):
                command.extend([
                    "-RefreshCookieFromBrowser",
                    "-Browser", str(douk.get("browser", "Firefox")),
                ])
            expected_hash = str(douk.get("expected_sha256", "")).strip()
            if expected_hash:
                command.extend(["-ExpectedSha256", expected_hash])
            return command

        ytdlp = self.config["yt_dlp"]
        platform_config = ytdlp.get("platforms", {}).get(job.platform, {})
        downloads = self.config["downloads"]
        output_dir = Path(downloads["directory"])
        output_dir.mkdir(parents=True, exist_ok=True)
        archive_path = Path(downloads["archive_file"])
        archive_path.parent.mkdir(parents=True, exist_ok=True)

        command = [
            ytdlp["executable"],
            "--ignore-config",
            "--encoding", "utf-8",
            "--no-playlist",
            "--newline",
            "--progress-delta", "1",
            "--progress-template",
            "download:PROGRESS:%(progress._percent_str)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s",
            "--download-archive", str(archive_path),
            "--print", "after_move:filepath=%(filepath)s",
            # --print implies quiet mode. Re-enable normal progress output so
            # the machine-readable progress template reaches the status API.
            "--no-quiet",
            "-o", str(output_dir / str(ytdlp.get("output_template", "%(title)s [%(id)s].%(ext)s"))),
        ]

        cookie_value = (
            cookie_override
            if override_cookie
            else (
                platform_config.get("cookies_from_browser")
                if "cookies_from_browser" in platform_config
                else (ytdlp.get("cookies_from_browser") if job.platform == "youtube" else "")
            )
        )
        proxy_value = (
            platform_config.get("proxy")
            if "proxy" in platform_config
            else (ytdlp.get("proxy") if job.platform == "youtube" else "")
        )
        option_values = (
            ("--proxy", proxy_value),
            ("--cookies-from-browser", cookie_value),
            ("--js-runtimes", ytdlp.get("js_runtime")),
            ("--ffmpeg-location", ytdlp.get("ffmpeg_location")),
            ("-f", ytdlp.get("format")),
            ("--merge-output-format", ytdlp.get("merge_output_format")),
        )
        for flag, value in option_values:
            if value not in (None, ""):
                command.extend([flag, str(value)])

        for component in ytdlp.get("remote_components", []):
            command.extend(["--remote-components", str(component)])
        fragments = int(ytdlp.get("concurrent_fragments", 1))
        if fragments > 1:
            command.extend(["--concurrent-fragments", str(fragments)])
        if ytdlp.get("restrict_filenames"):
            command.append("--restrict-filenames")
        if ytdlp.get("write_info_json"):
            command.append("--write-info-json")
        command.extend(str(value) for value in ytdlp.get("extra_args", []))
        command.append(job.url)
        return command

    @staticmethod
    def _backend(job: Job) -> str:
        return "douk" if job.platform == "douyin" else "ytdlp"

    def _next_runnable_index(self) -> int | None:
        if self.dispatch_paused:
            return None
        if len(self.active) >= self.max_total:
            return None
        active_by_backend = {
            backend: sum(1 for value in self.active.values() if value == backend)
            for backend in self.backend_limits
        }
        for index, job_id in enumerate(self.pending):
            job = self.jobs.get(job_id)
            if not job or job.status != "queued":
                continue
            backend = self._backend(job)
            if active_by_backend[backend] < self.backend_limits[backend]:
                return index
        return None

    def _dispatcher_loop(self) -> None:
        while True:
            with self.condition:
                while not self.stopping:
                    index = self._next_runnable_index()
                    if index is not None:
                        break
                    self.condition.wait(timeout=0.5)
                if self.stopping:
                    return
                job_id = self.pending.pop(index)
                job = self.jobs[job_id]
                backend = self._backend(job)
                self.active[job_id] = backend
                job.status = "running"
                job.stage = "starting"
                job.started_at = job.started_at or utc_now()
                job.finished_at = ""
                job.attempts += 1
                self._persist_job(job)
            future = self.executor.submit(self._run_job, job_id)
            future.add_done_callback(lambda completed, current_id=job_id: self._job_done(current_id, completed))

    def _job_done(self, job_id: str, future: Future[Any]) -> None:
        try:
            future.result()
        except Exception:
            self.logger.exception("Worker future failed job=%s", job_id)
        with self.condition:
            self.active.pop(job_id, None)
            job = self.jobs.get(job_id)
            if job and job.status == "retrying":
                # Requeue only after the old worker has released its active
                # slot. This prevents a zero-delay retry from racing its own
                # completion callback and corrupting concurrency accounting.
                self._schedule_retry(job_id)
            self.condition.notify_all()

    def _schedule_retry(self, job_id: str) -> None:
        def requeue() -> None:
            with self.condition:
                if self.stopping:
                    return
                job = self.jobs.get(job_id)
                if not job or job.status != "retrying":
                    return
                job.status = "queued"
                job.stage = "queued"
                self._persist_job(job)
                if job_id not in self.pending:
                    self.pending.append(job_id)
                self.condition.notify_all()

        timer = threading.Timer(self.retry_delay_seconds, requeue)
        timer.daemon = True
        timer.start()

    @staticmethod
    def _number(value: str, integer: bool = False) -> int | float | None:
        if not value or value in {"NA", "N/A", "None", "null"}:
            return None
        try:
            return int(float(value)) if integer else float(value)
        except ValueError:
            return None

    def _parse_progress(self, job: Job, line: str) -> None:
        clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        changed = False
        if clean.startswith("PROGRESS:"):
            parts = clean.removeprefix("PROGRESS:").split("|")
            if len(parts) >= 6:
                percent = self._number(parts[0].replace("%", "").strip())
                downloaded = self._number(parts[1], integer=True)
                total = self._number(parts[2], integer=True) or self._number(parts[3], integer=True)
                speed = self._number(parts[4])
                eta = self._number(parts[5], integer=True)
                job.progress_percent = min(100.0, max(0.0, float(percent))) if percent is not None else None
                job.downloaded_bytes = int(downloaded) if downloaded is not None else None
                job.total_bytes = int(total) if total is not None else None
                job.speed_bytes_per_second = float(speed) if speed is not None else None
                job.eta_seconds = int(eta) if eta is not None else None
                job.stage = "downloading"
                changed = True
        elif job.platform == "douyin":
            refresh_requested = bool(self.config["douk"].get("refresh_cookie_from_browser", False))
            if refresh_requested and re.search(
                r"读取指定浏览器|请输入浏览器名称|正在更新抖音参数|read.+Cookie",
                clean,
                re.IGNORECASE,
            ):
                job.stage = "refreshing-cookie"
                changed = True
            elif re.search(r"获取作品|采集|解析|作品数据|共提取到.+作品|开始处理", clean):
                job.stage = "extracting"
                changed = True
            elif re.search(r"开始下载作品文件|文件下载成功", clean):
                job.stage = "downloading"
                changed = True
            else:
                match = re.search(r"(?<!\d)(100|[1-9]?\d(?:\.\d+)?)\s*%", clean)
                if match:
                    job.progress_percent = float(match.group(1))
                    job.stage = "downloading"
                    changed = True
        if changed:
            job.updated_at = utc_now()
            now = time.monotonic()
            if now - self.last_progress_persist.get(job.id, 0.0) >= 1.0:
                with self.lock:
                    self.store.save(job)
                self.last_progress_persist[job.id] = now

    def _relocate_douk_outputs(self, job: Job) -> None:
        destination_value = str(self.config["douk"].get("output_directory", "")).strip()
        if not destination_value or not job.output_paths:
            return
        destination = Path(destination_value)
        destination.mkdir(parents=True, exist_ok=True)
        relocated: list[str] = []
        for raw_path in job.output_paths:
            source = Path(raw_path)
            if not source.is_file():
                raise FileNotFoundError(f"DouK reported output does not exist: {source}")
            if source.parent.resolve() == destination.resolve():
                relocated.append(str(source.resolve()))
                continue
            target = destination / source.name
            counter = 1
            while target.exists():
                target = destination / f"{source.stem} ({counter}){source.suffix}"
                counter += 1
            relocated.append(str(Path(shutil.move(str(source), str(target))).resolve()))
        job.output_paths = relocated
        job.output_path = relocated[0] if relocated else ""

    def _run_job(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs[job_id]

        def run_process(command: list[str]) -> tuple[int, list[str]]:
            output_lines: list[str] = []
            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process_encoding = "gbk" if os.name == "nt" and job.platform == "douyin" else "utf-8"
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding=process_encoding,
                errors="replace",
                creationflags=creation_flags,
            )
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.rstrip()
                output_lines.append(line)
                if len(output_lines) > 200:
                    output_lines.pop(0)
                if line.startswith("filepath="):
                    job.output_path = line.removeprefix("filepath=")
                    job.output_paths = [job.output_path]
                elif line.startswith("DOUK_RESULT_JSON="):
                    try:
                        result = json.loads(line.removeprefix("DOUK_RESULT_JSON="))
                        paths = [
                            str(item.get("path", ""))
                            for item in result.get("new_files", [])
                            if isinstance(item, dict) and item.get("path")
                        ]
                        job.output_paths = paths
                        job.output_path = paths[0] if paths else str(result.get("download_directory", ""))
                    except (json.JSONDecodeError, TypeError, ValueError):
                        self.logger.warning("Could not parse DouK result for job=%s", job.id)
                self._parse_progress(job, line)
                self.logger.info("job=%s %s", job.id, line)
            return process.wait(), output_lines

        command = self._build_command(job)
        self.logger.info("Starting job=%s video_id=%s", job.id, job.video_id)
        try:
            exit_code, output_lines = run_process(command)
            joined_output = "\n".join(output_lines)
            if (
                exit_code != 0
                and job.platform in {"youtube", "bilibili"}
                and "Could not copy Chrome cookie database" in joined_output
            ):
                platform_config = self.config["yt_dlp"].get("platforms", {}).get(job.platform, {})
                if "cookies_fallback_from_browser" in platform_config:
                    fallback = str(platform_config.get("cookies_fallback_from_browser", ""))
                    fallback_label = fallback or "anonymous"
                    self.logger.warning(
                        "Chrome cookie database is locked for job=%s; retrying with %s",
                        job.id,
                        fallback_label,
                    )
                    command = self._build_command(job, fallback, override_cookie=True)
                    exit_code, output_lines = run_process(command)
            with self.lock:
                job.exit_code = exit_code
                job.finished_at = utc_now()
                if exit_code == 0:
                    if job.platform == "douyin":
                        self._relocate_douk_outputs(job)
                    job.status = "completed"
                    job.stage = "completed"
                    job.progress_percent = 100.0
                    job.eta_seconds = 0
                    job.error = ""
                    self.logger.info("Completed job=%s output=%s", job.id, job.output_path or "archive-skip")
                elif job.attempts < job.max_attempts:
                    job.status = "retrying"
                    job.stage = "retrying"
                    job.error = "\n".join(output_lines[-20:])[-8000:]
                    self.logger.warning(
                        "Retrying job=%s attempt=%s/%s",
                        job.id,
                        job.attempts,
                        job.max_attempts,
                    )
                else:
                    job.status = "failed"
                    job.stage = "failed"
                    job.error = "\n".join(output_lines[-20:])[-8000:]
                    self.logger.error("Failed job=%s exit_code=%s", job.id, exit_code)
                self._persist_job(job)
        except Exception as exc:
            with self.lock:
                if job.attempts < job.max_attempts:
                    job.status = "retrying"
                    job.stage = "retrying"
                else:
                    job.status = "failed"
                    job.stage = "failed"
                job.finished_at = utc_now()
                job.error = str(exc)
                self._persist_job(job)
            self.logger.exception("Unhandled download failure job=%s", job.id)


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], manager: DownloadManager, config: dict[str, Any], logger: logging.Logger):
        super().__init__(address, RequestHandler)
        self.manager = manager
        self.config = config
        self.logger = logger


class RequestHandler(BaseHTTPRequestHandler):
    server: BridgeServer

    def log_message(self, fmt: str, *args: Any) -> None:
        self.server.logger.info("http %s - %s", self.client_address[0], fmt % args)

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin", "")
        if not origin:
            return True
        patterns = self.server.config["server"].get("allowed_origins", ["chrome-extension://*"])
        return any(fnmatch.fnmatchcase(origin, pattern) for pattern in patterns)

    def _authenticated(self) -> bool:
        expected = str(self.server.config["server"]["auth_token"])
        supplied = self.headers.get("X-YTDLP-Token", "")
        return hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin and self._origin_allowed():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json(self, status: HTTPStatus | int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _require_access(self) -> bool:
        if not self._origin_allowed():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Origin not allowed"})
            return False
        if not self._authenticated():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Invalid token"})
            return False
        return True

    def do_OPTIONS(self) -> None:
        if not self._origin_allowed():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Origin not allowed"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-YTDLP-Token")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            summary = self.server.manager.summary()
            self._json(HTTPStatus.OK, {
                "name": APP_NAME,
                "version": VERSION,
                "queueDepth": summary["queueDepth"],
                "active": summary["active"],
                "time": utc_now(),
            })
            return
        if path == "/api/jobs":
            if not self._require_access():
                return
            try:
                limit = max(1, min(200, int(parse_qs(parsed.query).get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            jobs = self.server.manager.list_jobs(limit)
            self._json(HTTPStatus.OK, {
                "jobs": [asdict(job) for job in jobs],
                "summary": self.server.manager.summary(),
            })
            return
        if path.startswith("/api/jobs/"):
            if not self._require_access():
                return
            job_id = path.removeprefix("/api/jobs/")
            job = self.server.manager.get(job_id)
            if not job:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Job not found"})
                return
            self._json(HTTPStatus.OK, {"job": asdict(job)})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        output_action_match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/(open|reveal)", path)
        if output_action_match:
            if not self._require_access():
                return
            job_id, action = output_action_match.groups()
            try:
                output_path = self.server.manager.launch_output(job_id, reveal=action == "reveal")
            except KeyError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Job not found"})
                return
            except ValueError as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
                return
            except FileNotFoundError as exc:
                self._json(HTTPStatus.GONE, {"error": f"Output file not found: {exc}"})
                return
            except OSError as exc:
                self.server.logger.exception("Could not launch output for job=%s", job_id)
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Could not launch output: {exc}"})
                return
            self._json(HTTPStatus.OK, {
                "ok": True,
                "action": action,
                "path": str(output_path),
            })
            return
        retry_match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/retry", path)
        if retry_match:
            if not self._require_access():
                return
            job = self.server.manager.retry(retry_match.group(1))
            if not job:
                self._json(HTTPStatus.CONFLICT, {"error": "Job is not failed or does not exist"})
                return
            self._json(HTTPStatus.ACCEPTED, {"accepted": True, "job": asdict(job)})
            return
        if path != "/api/download":
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if not self._require_access():
            return

        limit = int(self.server.config["server"].get("request_body_limit_bytes", 16384))
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
            return
        if length <= 0 or length > limit:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Invalid request body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON root must be an object")
            job, duplicate = self.server.manager.enqueue(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except (sqlite3.Error, OSError) as exc:
            self.server.logger.exception("Could not persist submitted job")
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": f"Could not persist job: {exc}"})
            return
        self._json(HTTPStatus.OK if duplicate else HTTPStatus.ACCEPTED, {
            "accepted": True,
            "duplicate": duplicate,
            "job": asdict(job),
        })


def configure_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def check_config(config: dict[str, Any]) -> None:
    command = [config["yt_dlp"]["executable"], "--version"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    if result.returncode != 0:
        raise SystemExit(f"yt-dlp self-check failed: {result.stderr.strip()}")
    print(json.dumps({
        "ok": True,
        "ytDlpVersion": result.stdout.strip(),
        "listen": f"http://{config['server']['host']}:{config['server']['port']}",
        "downloadDirectory": config["downloads"]["directory"],
        "proxyConfigured": bool(config["yt_dlp"].get("proxy")),
        "doukRoot": config["douk"]["root"],
        "doukRefreshBrowser": config["douk"].get("browser", "Firefox"),
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config.resolve())
    if args.check_config:
        check_config(config)
        return 0

    logger = configure_logging(Path(config["downloads"]["log_file"]))
    manager = DownloadManager(config, logger)
    address = (str(config["server"]["host"]), int(config["server"]["port"]))
    server = BridgeServer(address, manager, config, logger)
    manager.start()

    pid_path = args.config.resolve().parent / "listener.pid"
    pid_path.write_text(str(os.getpid()), encoding="ascii")

    def request_shutdown(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_shutdown)

    logger.info("Listening on http://%s:%s", *address)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        manager.stop()
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.info("Listener stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
