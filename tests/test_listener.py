import importlib.util
import json
import logging
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "listener" / "ytdlp_listener.py"
SPEC = importlib.util.spec_from_file_location("ytdlp_listener", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class YoutubeUrlTests(unittest.TestCase):
    def test_supported_urls(self):
        cases = [
            "https://www.youtube.com/watch?v=3IDn1iMxblo&t=4s",
            "https://youtu.be/3IDn1iMxblo?si=abc",
            "https://www.youtube.com/shorts/3IDn1iMxblo",
            "https://www.youtube.com/live/3IDn1iMxblo",
            "https://www.youtube.com/embed/3IDn1iMxblo",
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(MODULE.extract_video_id(url), "3IDn1iMxblo")

    def test_rejects_non_video_and_non_youtube_urls(self):
        cases = [
            "https://www.youtube.com/",
            "https://www.youtube.com/results?search_query=test",
            "https://example.com/watch?v=3IDn1iMxblo",
            "javascript:alert(1)",
            "https://youtu.be/not-valid",
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertIsNone(MODULE.extract_video_id(url))

    def test_canonical_url_drops_tracking_parameters(self):
        video_id, url = MODULE.canonical_url("https://youtu.be/3IDn1iMxblo?si=tracking")
        self.assertEqual(video_id, "3IDn1iMxblo")
        self.assertEqual(url, "https://www.youtube.com/watch?v=3IDn1iMxblo")

    def test_bilibili_video_urls(self):
        cases = [
            (
                "https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333.1007",
                ("bilibili", "BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD"),
            ),
            (
                "https://www.bilibili.com/video/BV1xx411c7mD?p=2&vd_source=tracking",
                ("bilibili", "BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD?p=2"),
            ),
            (
                "https://m.bilibili.com/video/av170001",
                ("bilibili", "av170001", "https://www.bilibili.com/video/av170001"),
            ),
            (
                "https://www.bilibili.com/bangumi/play/ep123456?from_spmid=tracking",
                ("bilibili", "ep123456", "https://www.bilibili.com/bangumi/play/ep123456"),
            ),
        ]
        for raw_url, expected in cases:
            with self.subTest(url=raw_url):
                self.assertEqual(MODULE.normalize_media_url(raw_url), expected)

    def test_rejects_bilibili_non_video_pages(self):
        for url in (
            "https://www.bilibili.com/",
            "https://search.bilibili.com/all?keyword=test",
            "https://space.bilibili.com/12345",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    MODULE.normalize_media_url(url)

    def test_douyin_video_and_note_urls(self):
        cases = [
            (
                "https://www.douyin.com/video/7666034873747967241?previous_page=web_code_link",
                ("douyin", "7666034873747967241", "https://www.douyin.com/video/7666034873747967241"),
            ),
            (
                "https://www.douyin.com/note/7652577852285603081",
                ("douyin", "7652577852285603081", "https://www.douyin.com/note/7652577852285603081"),
            ),
        ]
        for raw_url, expected in cases:
            with self.subTest(url=raw_url):
                self.assertEqual(MODULE.normalize_media_url(raw_url), expected)

    def test_rejects_douyin_non_media_pages(self):
        for url in (
            "https://www.douyin.com/",
            "https://www.douyin.com/jingxuan",
            "https://www.douyin.com/user/MS4wLjABAAAA",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    MODULE.normalize_media_url(url)


class PlatformCookieRoutingTests(unittest.TestCase):
    def make_manager(self, directory, platforms):
        config = {
            "downloads": {
                "directory": str(Path(directory) / "downloads"),
                "archive_file": str(Path(directory) / "archive.txt"),
                "jobs_file": str(Path(directory) / "jobs.json"),
                "database_file": str(Path(directory) / "jobs.db"),
            },
            "scheduler": {
                "max_total_concurrent": 3,
                "max_ytdlp_concurrent": 2,
                "max_douk_concurrent": 1,
                "max_attempts": 2,
                "retry_delay_seconds": 0,
            },
            "yt_dlp": {
                "executable": "yt-dlp.exe",
                "cookies_from_browser": "youtube-legacy-cookie",
                "platforms": platforms,
                "output_template": "%(id)s.%(ext)s",
            },
            "douk": {
                "root": str(Path(directory) / "DouK"),
                "wrapper": str(Path(directory) / "download_douyin.ps1"),
                "output_directory": str(Path(directory) / "final-douyin"),
                "powershell_executable": "powershell.exe",
                "refresh_cookie_from_browser": True,
                "browser": "Chrome",
                "expected_sha256": "A" * 64,
            },
        }
        return MODULE.DownloadManager(config, logging.getLogger("test"))

    def test_bilibili_never_inherits_legacy_youtube_cookie(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory, {"youtube": {"cookies_from_browser": "youtube-profile"}})
            job = MODULE.Job("1", "BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD", "", "", "", "bilibili")
            command = manager._build_command(job)
            self.assertNotIn("--cookies-from-browser", command)
            manager.store.close()

    def test_each_platform_uses_its_own_cookie_setting(self):
        platforms = {
            "youtube": {"cookies_from_browser": "firefox:youtube-profile"},
            "bilibili": {"cookies_from_browser": "firefox:bilibili-profile"},
        }
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory, platforms)
            youtube = MODULE.Job("1", "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            bilibili = MODULE.Job("2", "BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD", "", "", "", "bilibili")
            youtube_command = manager._build_command(youtube)
            bilibili_command = manager._build_command(bilibili)
            self.assertEqual(youtube_command[youtube_command.index("--cookies-from-browser") + 1], "firefox:youtube-profile")
            self.assertEqual(bilibili_command[bilibili_command.index("--cookies-from-browser") + 1], "firefox:bilibili-profile")
            manager.store.close()

    def test_each_platform_uses_its_own_proxy_setting(self):
        platforms = {
            "youtube": {"cookies_from_browser": "chrome", "proxy": "socks5h://127.0.0.1:10808"},
            "bilibili": {"cookies_from_browser": "chrome", "proxy": ""},
        }
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory, platforms)
            manager.config["yt_dlp"]["proxy"] = "http://legacy-shared-proxy.invalid:8080"
            youtube = MODULE.Job("1", "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            bilibili = MODULE.Job("2", "BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD", "", "", "", "bilibili")
            youtube_command = manager._build_command(youtube)
            bilibili_command = manager._build_command(bilibili)
            self.assertEqual(youtube_command[youtube_command.index("--proxy") + 1], "socks5h://127.0.0.1:10808")
            self.assertNotIn("--proxy", bilibili_command)
            manager.store.close()

    def test_legacy_global_proxy_only_falls_back_for_youtube(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory, {
                "youtube": {"cookies_from_browser": "chrome"},
                "bilibili": {"cookies_from_browser": "chrome"},
            })
            manager.config["yt_dlp"]["proxy"] = "socks5h://127.0.0.1:10808"
            youtube = MODULE.Job("1", "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            bilibili = MODULE.Job("2", "BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD", "", "", "", "bilibili")
            youtube_command = manager._build_command(youtube)
            bilibili_command = manager._build_command(bilibili)
            self.assertEqual(youtube_command[youtube_command.index("--proxy") + 1], "socks5h://127.0.0.1:10808")
            self.assertNotIn("--proxy", bilibili_command)
            manager.store.close()

    def test_ytdlp_command_reenables_progress_after_print(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory, {"youtube": {"cookies_from_browser": "firefox"}})
            job = MODULE.Job("1", "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            command = manager._build_command(job)
            self.assertIn("--progress-template", command)
            self.assertIn("--no-quiet", command)
            self.assertGreater(command.index("--no-quiet"), command.index("--print"))
            manager.store.close()

    def test_douyin_uses_douk_and_refreshes_chrome_cookie(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory, {
                "youtube": {"cookies_from_browser": "chrome"},
                "bilibili": {"cookies_from_browser": "chrome"},
            })
            job = MODULE.Job(
                "3",
                "7666034873747967241",
                "https://www.douyin.com/video/7666034873747967241",
                "",
                "",
                "",
                "douyin",
            )
            command = manager._build_command(job)
            self.assertIn("-RefreshCookieFromBrowser", command)
            self.assertEqual(command[command.index("-Browser") + 1], "Chrome")
            self.assertNotIn("--cookies-from-browser", command)
            manager.store.close()

    def test_douyin_cookie_refresh_is_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory, {})
            manager.config["douk"]["refresh_cookie_from_browser"] = False
            job = MODULE.Job("3", "7666034873747967241", "https://www.douyin.com/video/7666034873747967241", "", "", "", "douyin")
            command = manager._build_command(job)
            self.assertNotIn("-RefreshCookieFromBrowser", command)
            manager.store.close()

    def test_cookie_override_supports_browser_and_anonymous_fallbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory, {
                "youtube": {"cookies_from_browser": "chrome"},
                "bilibili": {"cookies_from_browser": "chrome"},
            })
            youtube = MODULE.Job("1", "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            bilibili = MODULE.Job("2", "BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD", "", "", "", "bilibili")
            firefox_command = manager._build_command(youtube, "firefox", override_cookie=True)
            anonymous_command = manager._build_command(bilibili, "", override_cookie=True)
            self.assertEqual(firefox_command[firefox_command.index("--cookies-from-browser") + 1], "firefox")
            self.assertNotIn("--cookies-from-browser", anonymous_command)
            manager.store.close()


class SchedulerTests(unittest.TestCase):
    def test_update_pause_preserves_new_queued_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            manager.pause_and_wait(timeout=0.1)
            job, _ = manager.enqueue({"url": "https://www.youtube.com/watch?v=3IDn1iMxblo"})
            self.assertIsNone(manager._next_runnable_index())
            self.assertEqual(manager.store.count(), 1)
            manager.resume_dispatch()
            self.assertEqual(manager._next_runnable_index(), 0)
            self.assertIn(job.id, manager.pending)
            manager.store.close()

    def test_update_wait_timeout_resumes_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            manager.active["busy"] = "douk"
            with self.assertRaises(TimeoutError):
                manager.pause_and_wait(timeout=0.01)
            self.assertFalse(manager.dispatch_paused)
            self.assertIn("busy", manager.active)
            manager.store.close()

    def make_manager(self, directory):
        return PlatformCookieRoutingTests().make_manager(
            directory,
            {
                "youtube": {"cookies_from_browser": "chrome"},
                "bilibili": {"cookies_from_browser": "chrome"},
            },
        )

    def test_enqueue_is_durable_before_acknowledgement_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.make_manager(directory)
            job, duplicate = first.enqueue({"url": "https://www.youtube.com/watch?v=3IDn1iMxblo"})
            self.assertFalse(duplicate)
            self.assertEqual(first.store.count(), 1)
            first.store.close()

            second = self.make_manager(directory)
            self.assertIn(job.id, second.jobs)
            self.assertIn(job.id, second.pending)
            self.assertEqual(second.jobs[job.id].status, "queued")
            second.store.close()

    def test_failed_database_commit_never_enters_memory_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            manager.store.save = lambda _job: (_ for _ in ()).throw(sqlite3.OperationalError("disk full"))
            with self.assertRaises(sqlite3.OperationalError):
                manager.enqueue({"url": "https://www.youtube.com/watch?v=3IDn1iMxblo"})
            self.assertEqual(manager.jobs, {})
            self.assertEqual(manager.video_jobs, {})
            self.assertEqual(manager.pending, [])
            manager.store.close()

    def test_startup_load_never_drops_old_unfinished_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            old_queued = MODULE.Job("old", "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            old_queued.created_at = "2020-01-01T00:00:00+00:00"
            recent_done = MODULE.Job("new", "jNQXAC9IVRw", "https://www.youtube.com/watch?v=jNQXAC9IVRw", "", "", "", "youtube")
            recent_done.created_at = "2026-01-01T00:00:00+00:00"
            recent_done.status = "completed"
            manager.store.save(old_queued)
            manager.store.save(recent_done)
            loaded = manager.store.load_for_startup(history_limit=1)
            self.assertEqual({item["id"] for item in loaded}, {"old", "new"})
            manager.store.close()

    def test_busy_douk_does_not_block_runnable_ytdlp_job(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            active_douk = MODULE.Job("a", "7666034873747967241", "https://www.douyin.com/video/7666034873747967241", "", "", "", "douyin")
            waiting_douk = MODULE.Job("b", "7652577852285603081", "https://www.douyin.com/note/7652577852285603081", "", "", "", "douyin")
            waiting_youtube = MODULE.Job("c", "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            manager.jobs = {job.id: job for job in (active_douk, waiting_douk, waiting_youtube)}
            manager.active = {active_douk.id: "douk"}
            manager.pending = [waiting_douk.id, waiting_youtube.id]
            self.assertEqual(manager._next_runnable_index(), 1)
            manager.store.close()

    def test_parses_ytdlp_progress_template(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            job = MODULE.Job("p", "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            manager._parse_progress(job, "PROGRESS: 42.5%|425|1000|NA|212.5|3")
            self.assertEqual(job.progress_percent, 42.5)
            self.assertEqual(job.downloaded_bytes, 425)
            self.assertEqual(job.total_bytes, 1000)
            self.assertEqual(job.speed_bytes_per_second, 212.5)
            self.assertEqual(job.eta_seconds, 3)
            manager.store.close()

    def test_douk_cookie_menu_is_not_misreported_as_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            manager.config["douk"]["refresh_cookie_from_browser"] = False
            job = MODULE.Job("d", "7666034873747967241", "https://www.douyin.com/video/7666034873747967241", "", "", "", "douyin")
            job.stage = "starting"
            manager._parse_progress(job, "2. 从浏览器读取 Cookie (抖音)")
            manager._parse_progress(job, "配置文件 cookie_tiktok 参数未设置")
            self.assertEqual(job.stage, "starting")
            manager._parse_progress(job, "共提取到 1 个作品，开始处理！")
            self.assertEqual(job.stage, "extracting")
            manager.store.close()

    def test_douk_refresh_stage_requires_refresh_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            manager.config["douk"]["refresh_cookie_from_browser"] = True
            job = MODULE.Job("d", "7666034873747967241", "https://www.douyin.com/video/7666034873747967241", "", "", "", "douyin")
            manager._parse_progress(job, "读取指定浏览器的 抖音 Cookie 并写入配置文件")
            self.assertEqual(job.stage, "refreshing-cookie")
            manager.store.close()

    def test_relocates_only_reported_douk_outputs_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            source_dir = Path(directory) / "DouK" / "Download"
            destination = Path(manager.config["douk"]["output_directory"])
            source_dir.mkdir(parents=True)
            destination.mkdir(parents=True)
            source = source_dir / "video.mp4"
            source.write_bytes(b"new")
            (destination / "video.mp4").write_bytes(b"existing")
            job = MODULE.Job("d", "7666034873747967241", "https://www.douyin.com/video/7666034873747967241", "", "", "", "douyin")
            job.output_paths = [str(source)]
            manager._relocate_douk_outputs(job)
            self.assertEqual(Path(job.output_path).name, "video (1).mp4")
            self.assertEqual(Path(job.output_path).read_bytes(), b"new")
            self.assertEqual((destination / "video.mp4").read_bytes(), b"existing")
            manager.store.close()


class OutputActionTests(unittest.TestCase):
    def make_manager(self, directory):
        return PlatformCookieRoutingTests().make_manager(directory, {})

    def test_completed_output_opens_with_default_application(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            video = Path(directory) / "finished video.mp4"
            video.write_bytes(b"video")
            job = MODULE.Job("a" * 32, "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            job.status = "completed"
            job.output_path = str(video)
            job.output_paths = [str(video)]
            manager.jobs[job.id] = job

            with patch.object(MODULE, "launch_output_path") as launch:
                result = manager.launch_output(job.id)

            self.assertEqual(result, video.resolve())
            launch.assert_called_once_with(video.resolve(), reveal=False)
            manager.store.close()

    def test_completed_output_can_be_revealed_in_file_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            video = Path(directory) / "finished.mp4"
            video.write_bytes(b"video")
            job = MODULE.Job("b" * 32, "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            job.status = "completed"
            job.output_path = str(video)
            manager.jobs[job.id] = job

            with patch.object(MODULE, "launch_output_path") as launch:
                manager.launch_output(job.id, reveal=True)

            launch.assert_called_once_with(video.resolve(), reveal=True)
            manager.store.close()

    def test_output_action_rejects_unfinished_or_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            job = MODULE.Job("c" * 32, "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            job.output_path = str(Path(directory) / "missing.mp4")
            manager.jobs[job.id] = job

            with self.assertRaisesRegex(ValueError, "not completed"):
                manager.launch_output(job.id)
            job.status = "completed"
            with self.assertRaises(FileNotFoundError):
                manager.launch_output(job.id)
            manager.store.close()

    def test_authenticated_output_api_launches_recorded_file(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory)
            video = Path(directory) / "api-finished.mp4"
            video.write_bytes(b"video")
            job = MODULE.Job("d" * 32, "3IDn1iMxblo", "https://www.youtube.com/watch?v=3IDn1iMxblo", "", "", "", "youtube")
            job.status = "completed"
            job.output_path = str(video)
            manager.jobs[job.id] = job
            manager.config["server"] = {
                "auth_token": "test-token",
                "allowed_origins": ["chrome-extension://*"],
            }
            server = MODULE.BridgeServer(("127.0.0.1", 0), manager, manager.config, logging.getLogger("test-api"))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/api/jobs/{job.id}/open"
            request = urllib.request.Request(
                url,
                data=b"",
                method="POST",
                headers={
                    "Origin": "chrome-extension://test-extension",
                    "X-YTDLP-Token": "test-token",
                },
            )
            try:
                with patch.object(MODULE, "launch_output_path") as launch:
                    with urllib.request.urlopen(request, timeout=2) as response:
                        payload = json.load(response)
                self.assertEqual(payload["action"], "open")
                launch.assert_called_once_with(video.resolve(), reveal=False)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                manager.store.close()


if __name__ == "__main__":
    unittest.main()
