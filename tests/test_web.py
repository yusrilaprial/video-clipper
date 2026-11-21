"""Uji web app (login, batas domain/durasi, antrean job, unduhan).  python tests/test_web.py"""
import importlib
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


def load_app(tmp, **env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("CLIPPER_")}
    clean.update({f"CLIPPER_{k}": str(v) for k, v in env.items()})
    clean["CLIPPER_OUTPUT_DIR"] = str(Path(tmp) / "out")
    patcher = mock.patch.dict(os.environ, clean, clear=True)
    patcher.start()
    config.get.cache_clear()
    import app
    importlib.reload(app)
    return app, patcher


class WebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patchers = []
        os.chdir(self.tmp.name)

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        config.get.cache_clear()
        self.tmp.cleanup()

    def app(self, **env):
        a, p = load_app(self.tmp.name, **env)
        self.patchers.append(p)
        return a, a.app.test_client()

    @staticmethod
    def payload(**kw):
        d = {"url": "https://www.youtube.com/watch?v=abc", "start": "0:10", "end": "0:40"}
        d.update(kw)
        return d

    def wait(self, c, jid, want=("done", "error"), timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            j = c.get(f"/api/job/{jid}").json
            if j["status"] in want:
                return j
            time.sleep(0.05)
        self.fail(f"job tidak selesai: {j}")

    def test_basic_auth(self):
        _, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        self.assertEqual(c.get("/").status_code, 401)
        self.assertEqual(c.get("/healthz").status_code, 200)
        self.assertEqual(c.post("/api/clip", json=self.payload()).status_code, 401)
        import base64
        ok = {"Authorization": "Basic " + base64.b64encode(b"budi:rahasia").decode()}
        bad = {"Authorization": "Basic " + base64.b64encode(b"budi:salah").decode()}
        self.assertEqual(c.get("/", headers=ok).status_code, 200)
        self.assertEqual(c.get("/", headers=bad).status_code, 401)

    def test_auth_requires_both(self):
        with self.assertRaises(config.ConfigError):
            self.app(AUTH_USER="budi")

    def test_allowed_domains_and_scheme(self):
        _, c = self.app(ALLOWED_DOMAINS="youtube.com, youtu.be")
        a = lambda u: c.post("/api/info", json={"url": u}).status_code  # noqa: E731
        self.assertEqual(a("https://evil.com/x"), 400)
        self.assertEqual(a("https://youtube.com.evil.com/x"), 400)
        self.assertEqual(a("ftp://youtube.com/x"), 400)
        self.assertEqual(a("file:///etc/passwd"), 400)
        r = c.post("/api/clip", json=self.payload(url="https://evil.com/v"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("Domain tidak diizinkan", r.json["error"])

    def test_max_clip_seconds(self):
        app, c = self.app(MAX_CLIP_SECONDS=1800)
        app.clip_auto = lambda *a, **k: Path(self.tmp.name) / "x.mp4"
        r = c.post("/api/clip", json=self.payload(start="0:00", end="1:00:00"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("maksimal 30 menit", r.json["error"])
        self.assertEqual(c.post("/api/clip", json=self.payload(start="0:00", end="1:00:00", test=True)).status_code, 200)

    def test_validation_errors(self):
        _, c = self.app()
        for bad in ({"frame": "bogus"}, {"fit": "x"}, {"subs": "x"}, {"sub_lang": "indonesia raya"},
                    {"quality": "9999"}, {"start": "5:00", "end": "1:00"}):
            with self.subTest(bad=bad):
                self.assertEqual(c.post("/api/clip", json=self.payload(**bad)).status_code, 400)

    def test_job_lifecycle_notes_and_downloads(self):
        app, c = self.app()
        out = Path(self.tmp.name) / "out" / "hasil [tiktok].mp4"
        seen = {}

        def fake(url, start, end, outdir, quality, precise=None, progress=None, mode=None, test=False, look=None):
            seen.update(look=look, test=test, url=url)
            progress({"status": "message", "level": "info", "message": "Subtitle: Whisper, bahasa terdeteksi 'id' (otomatis)"})
            progress({"status": "message", "level": "warning", "message": "peringatan uji"})
            out.write_bytes(b"VIDEO")
            out.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhalo\n\n")
            return out

        app.clip_auto = fake
        r = c.post("/api/clip", json=self.payload(frame="tiktok", fit="crop", subs="auto", sub_lang="auto", sub_mode="srt"))
        self.assertEqual(r.status_code, 200)
        jid = r.json["job_id"]
        j = self.wait(c, jid)
        self.assertEqual(j["status"], "done")
        self.assertEqual(seen["look"]["frame"], "tiktok")
        self.assertEqual(seen["look"]["subs"], "auto")
        self.assertEqual([n["level"] for n in j["notes"]], ["info", "warning"])
        self.assertTrue(j["has_srt"])
        self.assertNotIn(self.tmp.name, str(j))  # path server tidak bocor
        self.assertEqual(c.get(f"/download/{jid}").data, b"VIDEO")
        self.assertIn(b"halo", c.get(f"/download/{jid}/srt").data)
        self.assertEqual(c.get("/download/tidakada").status_code, 404)
        self.assertEqual(c.get("/api/job/tidakada").status_code, 404)

    def test_job_error_is_reported(self):
        app, c = self.app()

        def boom(*a, **k):
            raise RuntimeError("gagal unduh")
        app.clip_auto = boom
        j = self.wait(c, c.post("/api/clip", json=self.payload()).json["job_id"])
        self.assertEqual((j["status"], j["error"]), ("error", "gagal unduh"))

    def test_queue_limits_concurrency(self):
        app, c = self.app(MAX_JOBS=1)
        gate, running, peak = threading.Event(), [0], [0]
        lock = threading.Lock()

        def slow(*a, **k):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            gate.wait(5)
            with lock:
                running[0] -= 1
            return Path(self.tmp.name) / "x.mp4"

        app.clip_auto = slow
        j1 = c.post("/api/clip", json=self.payload()).json["job_id"]
        self.wait(c, j1, want=("running",))
        j2 = c.post("/api/clip", json=self.payload()).json["job_id"]
        time.sleep(0.3)
        self.assertEqual(c.get(f"/api/job/{j2}").json["status"], "queued")
        gate.set()
        self.wait(c, j1)
        self.wait(c, j2)
        self.assertEqual(peak[0], 1)

    def test_security_headers(self):
        _, c = self.app()
        h = c.get("/").headers
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main(verbosity=2)
