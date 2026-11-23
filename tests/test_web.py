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

    # ---------------------------------------------------------------- login
    @staticmethod
    def csrf_of(html):
        import re
        return re.search(r'name="csrf" value="([^"]+)"', html.decode()).group(1)

    def login(self, c, user="budi", pw="rahasia", **extra):
        tok = self.csrf_of(c.get("/login").data)
        return c.post("/login", data={"csrf": tok, "username": user, "password": pw, **extra})

    def test_unauthenticated_redirects_pages_and_401_for_api(self):
        _, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        r = c.get("/")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/login?next=/"))
        r = c.get("/download/abc")
        self.assertEqual(r.status_code, 302)
        r = c.post("/api/clip", json=self.payload())
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("WWW-Authenticate", r.headers)  # tidak memicu dialog bawaan browser
        self.assertIn("login", r.json)
        self.assertEqual(c.get("/healthz").status_code, 200)

    def test_login_page_is_a_real_form_without_alert(self):
        _, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        html = c.get("/login").data.decode()
        for needle in ('autocomplete="username"', 'autocomplete="current-password"', 'name="csrf"', 'type="password"',
                       'aria-label="Tampilkan kata sandi"', 'name="remember"'):
            self.assertIn(needle, html)
        root = Path(__file__).resolve().parent.parent / "templates"
        for f in root.glob("*.html"):
            self.assertNotIn("alert(", f.read_text(), f.name)
            self.assertNotIn("confirm(", f.read_text(), f.name)
            self.assertNotIn("prompt(", f.read_text(), f.name)

    def test_login_success_grants_session_and_redirects_to_next(self):
        _, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        tok = self.csrf_of(c.get("/login?next=/download/xyz").data)
        r = c.post("/login", data={"csrf": tok, "username": "budi", "password": "rahasia", "next": "/download/xyz"})
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/download/xyz"))
        self.assertEqual(c.get("/").status_code, 200)
        self.assertEqual(c.post("/api/info", json={"url": "ftp://x"}).status_code, 400)  # lolos auth, gagal validasi
        self.assertEqual(c.get("/login").status_code, 302)  # sudah login -> tidak perlu form

    def test_login_failure_shows_inline_error_keeps_username_not_password(self):
        _, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        r = self.login(c, "budi", "salah-sekali")
        self.assertEqual(r.status_code, 401)
        html = r.data.decode()
        self.assertIn("Nama pengguna atau kata sandi salah.", html)
        self.assertIn('value="budi"', html)
        self.assertNotIn("salah-sekali", html)
        self.assertIn('role="alert"', html)
        self.assertEqual(c.get("/").status_code, 302)  # tetap belum login

    def test_login_requires_valid_csrf(self):
        _, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        c.get("/login")
        r = c.post("/login", data={"csrf": "palsu", "username": "budi", "password": "rahasia"})
        self.assertEqual(r.status_code, 400)
        r = c.post("/login", data={"username": "budi", "password": "rahasia"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(c.get("/").status_code, 302)

    def test_open_redirect_is_blocked(self):
        _, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        for evil in ("//evil.com", "https://evil.com/x", "/\\evil.com", "javascript:alert(1)"):
            with self.subTest(evil=evil):
                c2 = c.application.test_client()
                tok = self.csrf_of(c2.get("/login").data)
                r = c2.post("/login", data={"csrf": tok, "username": "budi", "password": "rahasia", "next": evil})
                self.assertEqual(r.headers["Location"], "/")

    def test_bruteforce_lockout_blocks_even_correct_password(self):
        app, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia", LOGIN_MAX_ATTEMPTS=3, LOGIN_LOCK_SECONDS=60)
        with mock.patch.object(app.time, "sleep"):
            codes = [self.login(c, "budi", "x").status_code for _ in range(3)]
            self.assertEqual(codes, [401, 401, 429])  # percobaan ke-3 memicu kunci
            r = self.login(c, "budi", "rahasia")        # password benar pun ditolak selama dikunci
        self.assertEqual(r.status_code, 429)
        html = r.data.decode()
        self.assertIn("Coba lagi dalam", html)
        self.assertIn('data-retry="', html)
        self.assertEqual(c.get("/").status_code, 302)

    def test_logout_clears_session_and_requires_csrf(self):
        _, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        self.assertEqual(self.login(c).status_code, 302)
        page = c.get("/")
        self.assertIn(b'action="/logout"', page.data)
        tok = self.csrf_of(page.data)
        self.assertEqual(c.post("/logout", data={"csrf": "palsu"}).headers["Location"], "/")  # ditolak, tetap login
        self.assertEqual(c.get("/").status_code, 200)
        r = c.post("/logout", data={"csrf": tok})
        self.assertEqual(r.headers["Location"], "/login?bye=1")
        self.assertIn("Anda sudah keluar", c.get("/login?bye=1").data.decode())
        self.assertEqual(c.get("/").status_code, 302)

    def test_session_cookie_flags_and_remember(self):
        _, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia", COOKIE_SECURE="true")
        r = self.login(c, remember="1")
        cookie = r.headers["Set-Cookie"]
        for flag in ("HttpOnly", "SameSite=Lax", "Secure", "Expires="):
            self.assertIn(flag, cookie)
        _, c2 = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        self.assertNotIn("Expires=", self.login(c2).headers["Set-Cookie"])  # tanpa 'ingat saya' = cookie sesi

    def test_changing_password_invalidates_old_sessions(self):
        app, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        self.login(c)
        cookie = c.get_cookie("clipper_session").value
        _, c2 = self.app(AUTH_USER="budi", AUTH_PASSWORD="baru-lagi")
        c2.set_cookie("clipper_session", cookie)
        self.assertEqual(c2.get("/").status_code, 302)

    def test_basic_auth_still_works_for_scripts(self):
        _, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        import base64
        ok = {"Authorization": "Basic " + base64.b64encode(b"budi:rahasia").decode()}
        bad = {"Authorization": "Basic " + base64.b64encode(b"budi:salah").decode()}
        self.assertEqual(c.get("/", headers=ok).status_code, 200)
        self.assertEqual(c.get("/", headers=bad).status_code, 302)
        self.assertEqual(c.post("/api/info", json={"url": "ftp://x"}, headers=bad).status_code, 401)

    def test_login_route_without_auth_redirects_home(self):
        _, c = self.app()
        r = c.get("/login")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/"))
        self.assertNotIn(b'action="/logout"', c.get("/").data)  # tombol keluar hanya bila login aktif

    def test_security_headers_csp(self):
        _, c = self.app()
        h = c.get("/").headers
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])

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

    def test_job_progress_stage_and_percent_and_result_fields(self):
        app, c = self.app()
        out = Path(self.tmp.name) / "out" / "klip.mp4"
        mid, go = threading.Event(), threading.Event()

        def fake(url, start, end, outdir, quality, precise=None, progress=None, mode=None, test=False, look=None):
            progress({"status": "downloading", "downloaded_bytes": 250, "total_bytes": 1000})
            mid.set(); go.wait(5)
            progress({"status": "finished", "downloaded_bytes": 1048576})
            out.write_bytes(b"x" * 1234)
            return out

        app.clip_auto = fake
        jid = c.post("/api/clip", json=self.payload()).json["job_id"]
        self.assertTrue(mid.wait(5))
        j = c.get(f"/api/job/{jid}").json
        self.assertEqual((j["status"], j["stage"], j["percent"]), ("running", "download", 25.0))
        go.set()
        j = self.wait(c, jid)
        self.assertEqual((j["stage"], j["percent"], j["kind"], j["size"]), ("done", 100.0, "video", 1234))
        self.assertEqual(j["downloaded_mb"], 1.0)

    def test_percent_helper(self):
        app, _ = self.app()
        self.assertEqual(app._percent({"downloaded_bytes": 50, "total_bytes_estimate": 200}), 25.0)
        self.assertEqual(app._percent({"_percent_str": " 45.5%"}), 45.5)
        self.assertEqual(app._percent({"_percent_str": "\x1b[0;94m 12.0%\x1b[0m"}), 12.0)
        self.assertEqual(app._percent({"downloaded_bytes": 999, "total_bytes": 100}), 100.0)  # dibatasi 100
        self.assertIsNone(app._percent({}))

    def test_media_endpoint_streams_with_range_and_mime(self):
        app, c = self.app()
        out = Path(self.tmp.name) / "out" / "klip.mp4"
        mp3 = Path(self.tmp.name) / "out" / "klip.mp3"

        def fake(url, start, end, outdir, quality, precise=None, progress=None, mode=None, test=False, look=None):
            p = mp3 if quality == "audio" else out
            p.write_bytes(bytes(range(256)) * 4)
            return p

        app.clip_auto = fake
        jv = c.post("/api/clip", json=self.payload()).json["job_id"]
        ja = c.post("/api/clip", json=self.payload(quality="audio")).json["job_id"]
        self.wait(c, jv); self.wait(c, ja)
        r = c.get(f"/media/{jv}")
        self.assertEqual((r.status_code, r.mimetype), (200, "video/mp4"))
        self.assertNotIn("attachment", r.headers.get("Content-Disposition", ""))  # diputar inline
        r = c.get(f"/media/{jv}", headers={"Range": "bytes=10-19"})
        self.assertEqual((r.status_code, r.data), (206, bytes(range(10, 20))))
        self.assertEqual(c.get(f"/media/{ja}").mimetype, "audio/mpeg")
        self.assertEqual(c.get(f"/api/job/{ja}").json["kind"], "audio")
        self.assertEqual(c.get("/media/tidakada").status_code, 404)

    def test_media_requires_login_when_auth_enabled(self):
        app, c = self.app(AUTH_USER="budi", AUTH_PASSWORD="rahasia")
        self.assertEqual(c.get("/media/abc").status_code, 302)

    def test_index_renders_frame_cards_and_defaults(self):
        import json, re
        _, c = self.app(FRAME="3:4", QUALITY="720", SUBS="auto")
        html = c.get("/").data.decode()
        for fid in ("original", "tiktok", "reels", "shorts", "instagram", "square", "youtube", "twitter", "cinema", "custom"):
            self.assertIn(f'name="frame" value="{fid}"', html)
        self.assertEqual(html.count('name="quality"'), 6)
        d = json.loads(re.search(r"var D = (\{.*?\});\n", html).group(1))
        self.assertEqual((d["frame"], d["custom_frame"], d["quality"], d["subs"]), ("custom", "3:4", "720", "auto"))
        self.assertIn("viewport-fit=cover", html)
        self.assertIn('id="actionbar"', html)  # bilah aksi untuk ponsel

    def test_index_shows_max_clip_hint(self):
        _, c = self.app(MAX_CLIP_SECONDS=1800)
        self.assertIn("maks. 30 mnt", c.get("/").data.decode())

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
