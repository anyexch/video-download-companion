import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("dependency_updates", Path(__file__).parents[1] / "companion/dependency_updates.py")
U = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = U
SPEC.loader.exec_module(U)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.tools = self.app / "tools"
        self.tools.mkdir(parents=True)
        (self.tools / "yt-dlp.exe").write_bytes(b"1.0")
        for name, files in [("deno", ["deno.exe"]), ("ffmpeg", ["ffmpeg.exe", "ffprobe.exe"]), ("douk", ["main.exe"])]:
            (self.tools / name).mkdir()
            for filename in files:
                (self.tools / name / filename).write_bytes(b"1.0")
        self.config = self.root / "config.json"
        self.original = {"yt_dlp": {"executable": str(self.tools / "yt-dlp.exe"), "js_runtime": "deno:" + str(self.tools / "deno/deno.exe"), "ffmpeg_location": str(self.tools / "ffmpeg")},
                         "douk": {"root": str(self.tools / "douk"), "expected_sha256": "before", "cookie": "keep"}, "server": {"auth_token": "unchanged"}}
        U.write(self.config, self.original)
        self.assets = {}
        self.blobs = {}
        for name in U.SOURCES:
            self.set_asset(name, "2.0" if name != "douk" else "5.8", b"2.0")
        self.fetch_calls = []
        def fetch(name):
            self.fetch_calls.append(name)
            return dict(self.assets[name])
        def downloader(asset, dest, progress):
            dest.write_bytes(self.blobs[asset["name"]])
        def probe(name, target):
            path = target if name == "yt-dlp" else target / {"deno": "deno.exe", "ffmpeg": "ffmpeg.exe", "douk": "main.exe"}[name]
            return path.read_text()
        self.updater = U.Updater(self.app, self.config, fetch, downloader, probe)

    def set_asset(self, name, version, data):
        if name != "yt-dlp":
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                filenames = {"deno": ["deno.exe"], "ffmpeg": ["build/bin/ffmpeg.exe", "build/bin/ffprobe.exe"], "douk": ["DouK-Downloader.exe"]}[name]
                for filename in filenames:
                    archive.writestr(filename, data)
            data = stream.getvalue()
        self.blobs[name] = data
        self.assets[name] = {"name": name, "version": version, "url": "https://example.invalid/" + name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    def test_atomic_update_and_rollback_preserve_config(self):
        prepared = self.updater.prepare(self.assets["yt-dlp"])
        tx = self.updater.commit(prepared)
        self.assertEqual((self.tools / "yt-dlp.exe").read_bytes(), b"2.0")
        self.assertEqual((tx / "previous.exe").read_bytes(), b"1.0")
        self.updater.rollback("yt-dlp")
        self.assertEqual((self.tools / "yt-dlp.exe").read_bytes(), b"1.0")
        self.assertEqual(U.read(self.config), self.original)

    def test_download_hash_failure_does_not_touch_old_version(self):
        self.updater.downloader = lambda asset, path, progress: path.write_bytes(b"bad")
        with self.assertRaises(ValueError):
            self.updater.prepare(self.assets["yt-dlp"])
        self.assertEqual((self.tools / "yt-dlp.exe").read_bytes(), b"1.0")

    def test_changed_release_requires_new_check(self):
        stale = dict(self.assets["yt-dlp"])
        self.set_asset("yt-dlp", "3.0", b"3.0")
        with self.assertRaisesRegex(ValueError, "重新检查"):
            self.updater.prepare(stale)

    def test_post_swap_failure_rolls_back(self):
        prepared = self.updater.prepare(self.assets["yt-dlp"])
        def fail():
            raise RuntimeError("activation failed")
        with self.assertRaises(RuntimeError):
            self.updater.commit(prepared, reload=fail)
        self.assertEqual((self.tools / "yt-dlp.exe").read_bytes(), b"1.0")
        self.assertEqual(U.read(self.config), self.original)
        self.assertEqual(U.read(Path(prepared["tx"]) / "journal.json")["phase"], "recovered")

    def test_busy_download_cancels_before_swap(self):
        prepared = self.updater.prepare(self.assets["yt-dlp"])
        @contextlib.contextmanager
        def busy():
            raise TimeoutError("busy")
            yield
        with self.assertRaises(TimeoutError):
            self.updater.commit(prepared, quiet=busy)
        self.assertEqual((self.tools / "yt-dlp.exe").read_bytes(), b"1.0")

    def test_crash_recovery_between_directory_moves(self):
        prepared = self.updater.prepare(self.assets["yt-dlp"])
        tx = Path(prepared["tx"])
        U.write(tx / "journal.json", {"name": "yt-dlp", "phase": "prepared", "old_record": None, "old_config_hash": "before"})
        (self.tools / "yt-dlp.exe").rename(tx / "previous.exe")
        self.assertEqual(self.updater.recover(), ["yt-dlp"])
        self.assertEqual((self.tools / "yt-dlp.exe").read_bytes(), b"1.0")
        self.assertEqual(self.updater.recover(), [])

    def test_crash_after_metadata_written_restores_old(self):
        tx = self.updater.commit(self.updater.prepare(self.assets["yt-dlp"]))
        journal = U.read(tx / "journal.json")
        journal["phase"] = "swapped"
        U.write(tx / "journal.json", journal)
        self.updater.recover()
        self.assertEqual((self.tools / "yt-dlp.exe").read_bytes(), b"1.0")
        self.assertNotIn("yt-dlp", U.read(self.updater.state_path)["installed"])

    def test_failed_rename_preserves_old_binary(self):
        prepared = self.updater.prepare(self.assets["yt-dlp"])
        original_rename = Path.rename
        def rename(path, dest):
            if path == self.tools / "yt-dlp.exe":
                raise PermissionError("locked")
            return original_rename(path, dest)
        with patch.object(Path, "rename", rename), self.assertRaises(PermissionError):
            self.updater.commit(prepared)
        self.assertEqual((self.tools / "yt-dlp.exe").read_bytes(), b"1.0")

    def test_cache_skip_and_force(self):
        results = self.updater.check(force=True)
        self.assertEqual(len(self.fetch_calls), 4)
        self.updater.skip(results)
        result = self.updater.check()
        self.assertEqual(len(self.fetch_calls), 4)
        self.assertEqual(result[0]["status"], "skipped")
        self.updater.check(force=True)
        self.assertEqual(len(self.fetch_calls), 8)

    def test_offline_is_nonfatal_per_component(self):
        self.updater.fetch = lambda name: (_ for _ in ()).throw(OSError("offline"))
        rows = self.updater.check(force=True)
        self.assertTrue(all(r["status"] == "error" for r in rows))
        self.assertEqual((self.tools / "yt-dlp.exe").read_bytes(), b"1.0")

    def test_ffmpeg_pair_swapped_and_mutable_latest_detected(self):
        self.set_asset("ffmpeg", "latest", b"2.0")
        self.updater.commit(self.updater.prepare(self.assets["ffmpeg"]))
        self.assertEqual((self.tools / "ffmpeg/ffprobe.exe").read_bytes(), b"2.0")
        rows = {r["name"]: r for r in self.updater.check(force=True)}
        self.assertEqual(rows["ffmpeg"]["status"], "current")
        self.set_asset("ffmpeg", "latest", b"3.0")
        rows = {r["name"]: r for r in self.updater.check(force=True)}
        self.assertEqual(rows["ffmpeg"]["status"], "available")

    def test_deno_architecture_digits_are_not_version(self):
        self.assertEqual(U.version_tuple("deno 2.9.7 (stable, release, x86_64-pc-windows-msvc)"), (2, 9, 7))

    def test_unknown_douk_is_visible_but_not_installable(self):
        self.set_asset("douk", "5.9", b"5.9")
        rows = {r["name"]: r for r in self.updater.check(force=True)}
        self.assertEqual(rows["douk"]["status"], "blocked")
        with self.assertRaises(ValueError):
            self.updater.prepare(self.assets["douk"])

    def test_custom_install_path_is_not_modified(self):
        cfg = U.read(self.config)
        custom = self.root / "custom.exe"
        custom.write_bytes(b"1.0")
        cfg["yt_dlp"]["executable"] = str(custom)
        U.write(self.config, cfg)
        rows = {row["name"]: row for row in self.updater.check(force=True)}
        self.assertEqual(rows["yt-dlp"]["current"], "1.0")
        self.assertEqual(rows["yt-dlp"]["status"], "unmanaged")
        self.assertTrue(rows["yt-dlp"]["update_available"])
        self.assertFalse(rows["yt-dlp"]["can_adopt"])
        with self.assertRaisesRegex(ValueError, "纳管"):
            self.updater.prepare(self.assets["yt-dlp"])

    def test_custom_ytdlp_can_be_adopted_then_rolled_back_without_touching_source(self):
        custom = self.root / "custom-yt-dlp.exe"
        custom.write_bytes(b"1.0")
        (self.tools / "yt-dlp.exe").unlink()
        cfg = U.read(self.config)
        cfg["yt_dlp"]["executable"] = str(custom)
        U.write(self.config, cfg)

        rows = {row["name"]: row for row in self.updater.check(force=True)}
        self.assertTrue(rows["yt-dlp"]["can_adopt"])
        prepared = self.updater.prepare(rows["yt-dlp"]["asset"], adoption=True)
        tx = self.updater.commit(prepared)

        managed = self.tools / "yt-dlp.exe"
        self.assertEqual(custom.read_bytes(), b"1.0")
        self.assertEqual(managed.read_bytes(), b"2.0")
        self.assertEqual((tx / "previous.exe").read_bytes(), b"1.0")
        self.assertEqual(Path(U.read(self.config)["yt_dlp"]["executable"]), managed)
        self.assertEqual(U.read(self.updater.state_path)["installed"]["yt-dlp"]["adopted_from"], str(custom.resolve()))

        self.updater.rollback("yt-dlp")
        self.assertEqual(custom.read_bytes(), b"1.0")
        self.assertEqual(managed.read_bytes(), b"1.0")
        self.assertEqual(Path(U.read(self.config)["yt_dlp"]["executable"]), managed)

    def test_failed_adoption_restores_custom_config_and_removes_managed_target(self):
        custom = self.root / "custom-yt-dlp.exe"
        custom.write_bytes(b"1.0")
        (self.tools / "yt-dlp.exe").unlink()
        cfg = U.read(self.config)
        cfg["yt_dlp"]["executable"] = str(custom)
        U.write(self.config, cfg)
        prepared = self.updater.prepare(self.assets["yt-dlp"], adoption=True)

        with self.assertRaisesRegex(RuntimeError, "reload failed"):
            self.updater.commit(prepared, reload=lambda: (_ for _ in ()).throw(RuntimeError("reload failed")))

        self.assertEqual(custom.read_bytes(), b"1.0")
        self.assertFalse((self.tools / "yt-dlp.exe").exists())
        self.assertEqual(U.read(self.config)["yt_dlp"]["executable"], str(custom))
        self.assertEqual(U.read(Path(prepared["tx"]) / "journal.json")["phase"], "recovered")

    def test_crash_recovery_restores_external_path_after_adoption_swap(self):
        custom = self.root / "custom-yt-dlp.exe"
        custom.write_bytes(b"1.0")
        (self.tools / "yt-dlp.exe").unlink()
        cfg = U.read(self.config)
        cfg["yt_dlp"]["executable"] = str(custom)
        U.write(self.config, cfg)
        prepared = self.updater.prepare(self.assets["yt-dlp"], adoption=True)
        tx = Path(prepared["tx"])
        previous = tx / "previous.exe"
        shutil.copy2(custom, previous)
        journal = {"kind": "adoption", "name": "yt-dlp", "phase": "swapped",
                   "asset": self.assets["yt-dlp"], "old_record": None,
                   "old_config_values": {"executable": str(custom)},
                   "previous_fingerprints": self.updater.fingerprints("yt-dlp", custom)}
        U.write(tx / "journal.json", journal)
        (tx / "candidate.exe").rename(self.tools / "yt-dlp.exe")
        self.updater._set_config_values("yt-dlp", {"executable": str(self.tools / "yt-dlp.exe")})

        self.assertEqual(self.updater.recover(), ["yt-dlp"])
        self.assertEqual(custom.read_bytes(), b"1.0")
        self.assertFalse((self.tools / "yt-dlp.exe").exists())
        self.assertEqual(U.read(self.config)["yt_dlp"]["executable"], str(custom))
        self.assertEqual(self.updater.recover(), [])

    def test_custom_ffmpeg_adoption_leaves_external_directory_untouched(self):
        custom = self.root / "custom-ffmpeg"
        custom.mkdir()
        (custom / "ffmpeg.exe").write_bytes(b"1.0")
        (custom / "ffprobe.exe").write_bytes(b"1.0")
        (custom / "user-note.txt").write_text("keep")
        shutil.rmtree(self.tools / "ffmpeg")
        cfg = U.read(self.config)
        cfg["yt_dlp"]["ffmpeg_location"] = str(custom)
        U.write(self.config, cfg)

        prepared = self.updater.prepare(self.assets["ffmpeg"], adoption=True)
        tx = self.updater.commit(prepared)

        self.assertEqual((custom / "user-note.txt").read_text(), "keep")
        self.assertEqual((custom / "ffmpeg.exe").read_bytes(), b"1.0")
        self.assertEqual((self.tools / "ffmpeg/ffmpeg.exe").read_bytes(), b"2.0")
        self.assertEqual((tx / "previous/user-note.txt").read_text(), "keep")
        self.assertEqual(Path(U.read(self.config)["yt_dlp"]["ffmpeg_location"]), self.tools / "ffmpeg")

    def test_unverified_custom_douk_cannot_be_adopted(self):
        self.set_asset("douk", "5.9", b"5.9")
        custom = self.root / "custom-douk"
        custom.mkdir()
        (custom / "main.exe").write_bytes(b"5.9")
        shutil.rmtree(self.tools / "douk")
        cfg = U.read(self.config)
        cfg["douk"]["root"] = str(custom)
        U.write(self.config, cfg)

        rows = {row["name"]: row for row in self.updater.check(force=True)}
        self.assertEqual(rows["douk"]["status"], "unmanaged")
        self.assertFalse(rows["douk"]["can_adopt"])
        self.assertIn("未经兼容性验证", rows["douk"]["message"])

    def test_non_deno_runtime_reports_its_version_without_fetching_deno(self):
        cfg = U.read(self.config)
        node = self.root / "node.exe"
        node.write_bytes(b"node")
        cfg["yt_dlp"]["js_runtime"] = "node:" + str(node)
        U.write(self.config, cfg)
        self.updater.runtime_probe = lambda kind, path: f"{kind} v22.0.0"
        rows = {row["name"]: row for row in self.updater.check(force=True)}
        self.assertEqual(rows["deno"]["current"], "node v22.0.0")
        self.assertEqual(rows["deno"]["status"], "alternate")
        self.assertEqual(rows["deno"]["display_name"], "js-runtime (node)")
        self.assertFalse(rows["deno"].get("can_adopt", False))
        self.assertNotIn("deno", self.fetch_calls)

    def test_javascript_runtime_keeps_windows_drive_colon_in_path(self):
        kind, path = U.javascript_runtime(r"node:C:\Program Files\nodejs\node.exe")
        self.assertEqual(kind, "node")
        self.assertTrue(str(path).endswith(r"Program Files\nodejs\node.exe"))

    def test_corrupt_rollback_backup_is_rejected(self):
        tx = self.updater.commit(self.updater.prepare(self.assets["yt-dlp"]))
        (tx / "previous.exe").write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.updater.rollback("yt-dlp")
        self.assertEqual((self.tools / "yt-dlp.exe").read_bytes(), b"2.0")

    def test_archive_traversal_and_alternate_streams_rejected(self):
        for name in ["../escape", "C:/escape", "nested/file:stream", "..\\escape"]:
            archive = self.root / "evil.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(name, b"x")
            with self.subTest(name=name), self.assertRaises(ValueError):
                U.extract(archive, self.root / "unpack")

    def test_douk_data_migration_and_rollback_keep_latest_user_data(self):
        old_hash = U.sha(self.tools / "douk/main.exe")
        self.set_asset("douk", "5.8", b"5.8")
        new_hash = hashlib.sha256(b"5.8").hexdigest()
        volume = self.tools / "douk/_internal/Volume"
        volume.mkdir(parents=True)
        (volume / "settings.json").write_text('{"cookie":"keep"}')
        (volume / "history.db").write_bytes(b"history")
        with patch.dict(U.DOUK_HASHES, {"5.7": old_hash, "5.8": new_hash}):
            self.updater.commit(self.updater.prepare(self.assets["douk"]))
            current = self.tools / "douk/Volume"
            self.assertEqual((current / "history.db").read_bytes(), b"history")
            (current / "history.db").write_bytes(b"new history")
            self.updater.rollback("douk")
        self.assertEqual((volume / "history.db").read_bytes(), b"new history")
        self.assertEqual(U.read(volume / "settings.json")["cookie"], "keep")
        self.assertEqual(U.read(self.config)["server"]["auth_token"], "unchanged")
        self.assertEqual(U.read(self.config)["douk"]["expected_sha256"], old_hash.upper())
        self.assertTrue((self.tools / "douk-5.7-cookie-reader/main.exe").exists())

    def test_metadata_requires_official_source_and_digest(self):
        release = {"tag_name": "2.0", "assets": [{"name": "yt-dlp.exe", "digest": "sha256:" + "a" * 64, "size": 3, "browser_download_url": "https://github.com/yt-dlp/yt-dlp/releases/download/2.0/yt-dlp.exe"}]}
        self.assertEqual(U.release_asset("yt-dlp", release)["version"], "2.0")
        release["assets"][0]["browser_download_url"] = "https://evil.invalid/yt-dlp.exe"
        with self.assertRaises(ValueError):
            U.release_asset("yt-dlp", release)
        release["assets"][0]["digest"] = ""
        with self.assertRaises(ValueError):
            U.release_asset("yt-dlp", release)


if __name__ == "__main__":
    unittest.main()
