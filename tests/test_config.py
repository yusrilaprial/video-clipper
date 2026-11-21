"""Uji konfigurasi env/.env.  Jalankan: python tests/test_config.py"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


def env(**kw):
    """Isolasi: hapus semua CLIPPER_*, set yang diminta, pindah ke folder kosong (tanpa .env)."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("CLIPPER_")}
    clean.update({f"CLIPPER_{k}": str(v) for k, v in kw.items()})
    return mock.patch.dict(os.environ, clean, clear=True)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        config.get.cache_clear()

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()
        config.get.cache_clear()

    def test_defaults(self):
        with env():
            c = config.load()
        self.assertEqual((c.output_dir, c.quality, c.mode, c.precise), ("output", "best", "auto", True))
        self.assertEqual((c.live_workers, c.live_margin, c.port, c.host), (4, 1, 5000, "127.0.0.1"))
        self.assertEqual((c.video_preset, c.video_crf, c.audio_bitrate), ("veryfast", 20, "192k"))

    def test_env_override_and_types(self):
        with env(QUALITY="720", PRECISE="no", LIVE_WORKERS=8, PORT=8080, TEST_SECONDS="5.5", MODE="LIVE"):
            c = config.load()
        self.assertEqual((c.quality, c.precise, c.live_workers, c.port, c.test_seconds, c.mode),
                         ("720", False, 8, 8080, 5.5, "live"))

    def test_dotenv_file_and_priority(self):
        Path(".env").write_text(
            "# komentar\nCLIPPER_QUALITY=480\nexport CLIPPER_OUTPUT_DIR='hasil klip'\n"
            "CLIPPER_PORT=9000  # inline\nCLIPPER_MODE=vod\n", encoding="utf-8")
        with env(PORT="7000"):  # env asli menang atas .env
            c = config.load()
        self.assertEqual((c.quality, c.output_dir, c.port, c.mode), ("480", "hasil klip", 7000, "vod"))

    def test_explicit_env_file(self):
        Path("lain.env").write_text("CLIPPER_QUALITY=360\n")
        with env(ENV_FILE="lain.env"):
            self.assertEqual(config.load().quality, "360")
        with env(ENV_FILE="tidak-ada.env"):
            with self.assertRaises(config.ConfigError):
                config.load()

    def test_invalid_values(self):
        for k, v in [("PORT", "abc"), ("PORT", "70000"), ("QUALITY", "9999"), ("PRECISE", "mungkin"),
                     ("AUDIO_BITRATE", "tinggi"), ("VIDEO_CRF", "99"), ("VIDEO_PRESET", "kilat"),
                     ("LIVE_WORKERS", "0"), ("MODE", "xyz")]:
            with self.subTest(k=k, v=v), env(**{k: v}):
                with self.assertRaises(config.ConfigError) as cm:
                    config.load()
                self.assertIn(f"CLIPPER_{k}", str(cm.exception))

    def test_new_defaults_frame_subs_server(self):
        with env():
            c = config.get.__wrapped__()
        self.assertEqual((c.frame, c.fit, c.focus, c.subs, c.sub_lang, c.sub_mode), ("original", "blur", 0.5, "off", "auto", "burn"))
        self.assertEqual((c.whisper_model, c.whisper_device, c.max_jobs, c.max_clip_seconds, c.retention_hours),
                         ("base", "auto", 2, 0, 0.0))
        self.assertEqual((c.auth_user, c.allowed_domains), (None, ()))

    def test_new_env_values(self):
        with env(FRAME="TikTok", FIT="crop", FOCUS="0.2", SUBS="auto", SUB_LANG="ID", SUB_MODE="soft",
                 WHISPER_MODEL="small", ALLOWED_DOMAINS="YouTube.com, .youtu.be ", MAX_JOBS=3,
                 AUTH_USER="a", AUTH_PASSWORD="b", RETENTION_HOURS="1.5"):
            c = config.get.__wrapped__()
        self.assertEqual((c.frame, c.fit, c.focus, c.subs, c.sub_lang, c.sub_mode),
                         ("tiktok", "crop", 0.2, "auto", "id", "soft"))
        self.assertEqual(c.allowed_domains, ("youtube.com", "youtu.be"))
        self.assertEqual((c.whisper_model, c.max_jobs, c.retention_hours), ("small", 3, 1.5))

    def test_new_invalid_values(self):
        for k, v in [("FRAME", "bogus"), ("FRAME", "10:1"), ("FIT", "stretch"), ("FOCUS", "2"), ("SUBS", "ya"),
                     ("SUB_MODE", "x"), ("SUB_SCALE", "9"), ("WHISPER_DEVICE", "tpu"), ("MAX_JOBS", "0")]:
            with self.subTest(k=k, v=v), env(**{k: v}):
                with self.assertRaises(config.ConfigError) as cm:
                    config.get.__wrapped__()
                self.assertIn(f"CLIPPER_{k}", str(cm.exception))

    def test_describe_masks_auth_password(self):
        with env(AUTH_USER="a", AUTH_PASSWORD="rahasia"):
            d = config.describe(config.get.__wrapped__())
        self.assertEqual(d["auth_password"], "***")

    def test_bad_dotenv_line(self):
        Path(".env").write_text("INI BUKAN KEY VALUE\n")
        with env():
            with self.assertRaises(config.ConfigError):
                config.load()

    def test_cookies_file_must_exist(self):
        with env(COOKIES_FILE="nope.txt"):
            with self.assertRaises(config.ConfigError):
                config.load()

    def test_ffmpeg_dir_and_file(self):
        d = Path("bin"); d.mkdir()
        with env(FFMPEG_PATH=str(d)):
            c = config.load()
        self.assertEqual(Path(c.ffmpeg).parent, d)
        self.assertEqual(Path(c.ffprobe).parent, d)
        self.assertEqual(c.ffmpeg_location, str(d))
        with env(FFMPEG_PATH="/opt/x/ffmpeg-custom", FFPROBE_PATH="/opt/x/probe"):
            c = config.load()
        self.assertEqual((c.ffmpeg, c.ffprobe), ("/opt/x/ffmpeg-custom", "/opt/x/probe"))

    def test_describe_masks_proxy_password(self):
        with env(PROXY="http://budi:rahasia@10.0.0.1:8080"):
            d = config.describe(config.load())
        self.assertNotIn("rahasia", d["proxy"])
        self.assertIn("budi:***@10.0.0.1:8080", d["proxy"])

    def test_ydl_opts_follow_config(self):
        import clipper
        Path("c.txt").write_text("# Netscape HTTP Cookie File\n")
        with env(PROXY="http://p:1", COOKIES_FILE="c.txt", FFMPEG_PATH="/opt/ffmpeg"):
            config.reload()
            o = clipper.ydl_base_opts()
        self.assertEqual((o["proxy"], o["ffmpeg_location"]), ("http://p:1", "/opt/ffmpeg"))
        self.assertTrue(o["cookiefile"].endswith("c.txt"))
        with env(COOKIES_FROM_BROWSER="firefox:default"):
            config.reload()
            self.assertEqual(clipper.ydl_base_opts()["cookiesfrombrowser"], ("firefox", "default"))

    def test_cli_show_config_and_error(self):
        import io, contextlib, cli
        with env(QUALITY="720", PORT="8123"), mock.patch.object(sys, "argv", ["cli.py", "--show-config"]):
            config.reload()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main()
        self.assertEqual(rc, 0)
        self.assertIn('"quality": "720"', buf.getvalue())
        self.assertIn('"port": 8123', buf.getvalue())
        with env(PORT="abc"), mock.patch.object(sys, "argv", ["cli.py", "--show-config"]):
            config.get.cache_clear()
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = cli.main()
        self.assertEqual(rc, 2)
        self.assertIn("CLIPPER_PORT", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
