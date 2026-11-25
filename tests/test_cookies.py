"""Uji cookies YouTube, penerjemah error, dan salinan cookies yt-dlp.  python tests/test_cookies.py"""
import importlib
import io
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

USER_ERROR = ("ERROR: [youtube] ty-bTl8iD7c: Sign in to confirm you\u2019re not a bot. This helps protect our community. "
              "Use --cookies-from-browser or --cookies for the authentication. See https://github.com/yt-dlp/yt-dlp/wiki/FAQ")


def cookie_line(domain, name, exp, value="v", http_only=False):
    return ("#HttpOnly_" if http_only else "") + f"{domain}\tTRUE\t/\tTRUE\t{exp}\t{name}\t{value}"


def make_cookies(login=True, exp=None, extra=""):
    exp = exp if exp is not None else int(time.time()) + 86400 * 200
    lines = ["# Netscape HTTP Cookie File", cookie_line(".youtube.com", "VISITOR_INFO1_LIVE", exp)]
    if login:
        lines += [cookie_line(".youtube.com", "SID", exp), cookie_line(".youtube.com", "__Secure-3PSID", exp, http_only=True)]
    return "\n".join(lines) + "\n" + extra


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        clean = {k: v for k, v in os.environ.items() if not k.startswith("CLIPPER_")}
        clean["CLIPPER_OUTPUT_DIR"] = str(Path(self.tmp.name) / "data" / "output")
        self.patch = mock.patch.dict(os.environ, clean, clear=True)
        self.patch.start()
        config.get.cache_clear()

    def tearDown(self):
        self.patch.stop()
        os.chdir(self.cwd)
        config.get.cache_clear()
        self.tmp.cleanup()

    def env(self, **kw):
        os.environ.update({f"CLIPPER_{k}": str(v) for k, v in kw.items()})
        config.get.cache_clear()


class ExplainTest(unittest.TestCase):
    def test_exact_user_error_is_recognised(self):
        import yterrors
        d = yterrors.explain(USER_ERROR, cookies_active=False)
        self.assertEqual(d["code"], "bot_check")
        self.assertIn("anti-bot", d["message"])
        self.assertIn("Cookies", d["hint"])
        self.assertIn("not a bot", d["detail"])

    def test_hint_differs_when_cookies_already_present(self):
        import yterrors
        self.assertIn("kedaluwarsa", yterrors.explain(USER_ERROR, True)["hint"])
        self.assertNotIn("kedaluwarsa", yterrors.explain(USER_ERROR, False)["hint"])

    def test_other_known_errors(self):
        import yterrors
        cases = {
            "ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users.": "age",
            "ERROR: [youtube] x: Private video. Sign in if you've been granted access to this video": "private",
            "ERROR: [youtube] x: Video unavailable": "unavailable",
            "ERROR: [youtube] x: The uploader has not made this video available in your country": "geo",
            "ERROR: [youtube] x: This live event will begin in 2 hours.": "upcoming",
            "ERROR: unable to download video data: HTTP Error 429: Too Many Requests": "rate_limit",
            "ERROR: [youtube] x: Join this channel to get access to members-only content": "members",
            "ERROR: Requested format is not available": "format",
            "ERROR: Unable to download webpage: <urlopen error [Errno -3] Temporary failure in name resolution>": "network",
        }
        for raw, code in cases.items():
            with self.subTest(code=code):
                self.assertEqual(yterrors.explain(raw, False)["code"], code)

    def test_unknown_error_passes_through_cleaned(self):
        import yterrors
        d = yterrors.explain("ERROR: [generic] abc: sesuatu yang aneh", False)
        self.assertEqual((d["code"], d["message"], d["hint"]), ("other", "sesuatu yang aneh", ""))
        self.assertEqual(yterrors.explain("Link harus diawali http://", False)["message"], "Link harus diawali http://")
        self.assertNotIn("\x1b", yterrors.explain("\x1b[0;31mERROR:\x1b[0m Video unavailable", False)["detail"])


class CookieModuleTest(Base):
    def test_inspect_valid_with_login(self):
        import cookies as ck
        i = ck.inspect(make_cookies())
        self.assertEqual((i["count"], i["youtube"], i["login"], i["warnings"]), (3, 3, True, []))
        self.assertGreater(i["expires"], time.time())

    def test_httponly_prefix_is_parsed(self):
        import cookies as ck
        only = "# Netscape HTTP Cookie File\n" + cookie_line(".youtube.com", "SID", 2000000000, http_only=True) + "\n"
        self.assertTrue(ck.inspect(only)["login"])

    def test_warnings_for_no_login_and_expired(self):
        import cookies as ck
        self.assertIn("login", ck.inspect(make_cookies(login=False))["warnings"][0])
        self.assertIn("kedaluwarsa", ck.inspect(make_cookies(exp=1000000000))["warnings"][0])

    def test_rejects_bad_files(self):
        import cookies as ck
        bad = {"": "kosong", "[{\"name\":\"SID\"}]": "JSON", "bukan cookies sama sekali": "terbaca",
               "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\ta\tb\n": "youtube.com"}
        for text, frag in bad.items():
            with self.subTest(text=text[:20]), self.assertRaises(ck.CookieError) as cm:
                ck.inspect(text)
            self.assertIn(frag.lower(), str(cm.exception).lower())

    def test_normalize_adds_header_and_fixes_newlines(self):
        import cookies as ck
        raw = cookie_line(".youtube.com", "SID", 2000000000).replace("\t", "\t") + "\r\n"
        out = ck.normalize(raw)
        self.assertTrue(out.startswith("# Netscape HTTP Cookie File\n"))
        self.assertNotIn("\r", out)
        self.assertEqual(ck.normalize(out), out)  # idempotent

    def test_save_permissions_atomic_and_location(self):
        import cookies as ck
        ck.save(make_cookies().encode())
        p = ck.managed_path()
        self.assertEqual(p, Path(self.tmp.name) / "data" / "cookies.txt")  # di sebelah folder output (= volume /data di Docker)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertFalse(p.with_name("cookies.txt.tmp").exists())
        self.assertIn("SID", p.read_text())

    def test_save_rejects_large_and_binary(self):
        import cookies as ck
        with self.assertRaises(ck.CookieError):
            ck.save(b"x" * (ck.MAX_BYTES + 1))
        with self.assertRaises(ck.CookieError):
            ck.save(b"\xff\xfe\x00\x01")
        self.assertFalse(ck.managed_path().exists())  # tidak ada file setengah jadi

    def test_status_and_delete_never_leak_values(self):
        import cookies as ck
        self.assertEqual(ck.status(True), {"configured": False, "source": None, "can_manage": True, "uploaded": False})
        ck.save(make_cookies().replace("\tv\n", "\tRAHASIA-NILAI\n").encode())
        st = ck.status(True)
        self.assertTrue(st["configured"] and st["uploaded"] and st["login"])
        self.assertNotIn("RAHASIA-NILAI", str(st))
        self.assertTrue(ck.delete())
        self.assertFalse(ck.delete())
        self.assertFalse(ck.status(True)["configured"])

    def test_source_priority(self):
        import cookies as ck
        Path("c.txt").write_text(make_cookies())
        self.env(COOKIES_FILE="c.txt")
        self.assertEqual(ck.active_source(), "file")
        ck.save(make_cookies().encode())
        self.assertEqual(ck.active_source(), "upload")  # unggahan web mengalahkan file env
        ck.delete()
        os.environ.pop("CLIPPER_COOKIES_FILE"); self.env(COOKIES_FROM_BROWSER="chrome")
        self.assertEqual(ck.active_source(), "browser")


class YtdlpSessionTest(Base):
    def test_player_clients_option(self):
        import clipper
        self.env(YT_PLAYER_CLIENTS="tv, web_safari")
        self.assertEqual(clipper.ydl_base_opts()["extractor_args"], {"youtube": {"player_client": ["tv", "web_safari"]}})
        self.env(YT_PLAYER_CLIENTS="tv;rm -rf")
        with self.assertRaises(config.ConfigError):
            config.get()

    def test_uploaded_cookies_used_by_ytdlp_opts(self):
        import clipper, cookies as ck
        ck.save(make_cookies().encode())
        self.assertEqual(clipper.ydl_base_opts()["cookiefile"], str(ck.managed_path()))

    def test_session_uses_throwaway_copy_and_cleans_up(self):
        import clipper, cookies as ck
        ck.save(make_cookies().encode())
        src = ck.managed_path()
        os.utime(src, (1000, 1000))
        with clipper.ydl_session({"skip_download": True}) as opts:
            tmp = Path(opts["cookiefile"])
            self.assertNotEqual(tmp, src)
            self.assertTrue(tmp.is_file())
            self.assertEqual(stat.S_IMODE(tmp.stat().st_mode), 0o600)
            self.assertEqual(tmp.read_text(), src.read_text())
            self.assertTrue(opts["skip_download"])
            import yt_dlp
            with yt_dlp.YoutubeDL(opts):  # yt-dlp menulis ulang file cookies saat ditutup...
                pass
        self.assertFalse(tmp.exists())              # ...salinan dibersihkan
        self.assertEqual(src.stat().st_mtime, 1000)  # ...dan file asli tidak tersentuh

    def test_session_without_cookies_has_no_cookiefile(self):
        import clipper
        with clipper.ydl_session() as opts:
            self.assertNotIn("cookiefile", opts)

    def test_concurrent_sessions_get_distinct_copies(self):
        import clipper, cookies as ck
        ck.save(make_cookies().encode())
        with clipper.ydl_session() as a, clipper.ydl_session() as b:
            self.assertNotEqual(a["cookiefile"], b["cookiefile"])


class WebCookieTest(Base):
    def app(self, **env):
        self.env(**env)
        import app
        importlib.reload(app)
        return app, app.app.test_client()

    def upload(self, c, text=None, headers=None, name="cookies.txt"):
        data = {"file": (io.BytesIO((text if text is not None else make_cookies()).encode()), name)}
        return c.post("/api/cookies", data=data, content_type="multipart/form-data",
                      headers={"X-Clipper": "1"} if headers is None else headers)

    def test_status_upload_delete_flow(self):
        app, c = self.app()
        self.assertFalse(c.get("/api/cookies").json["configured"])
        r = self.upload(c)
        self.assertEqual(r.status_code, 200)
        j = r.json
        self.assertTrue(j["configured"] and j["uploaded"] and j["login"])
        self.assertTrue(c.get("/api/cookies").json["uploaded"])
        r = c.delete("/api/cookies", headers={"X-Clipper": "1"})
        self.assertFalse(r.json["configured"])

    def test_bad_uploads_rejected_with_message(self):
        app, c = self.app()
        r = self.upload(c, text="bukan cookies")
        self.assertEqual(r.status_code, 400)
        self.assertIn("cookie", r.json["error"].lower())
        r = c.post("/api/cookies", data={}, content_type="multipart/form-data", headers={"X-Clipper": "1"})
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/cookies", data={"file": (io.BytesIO(b"x" * (3 * 1024 * 1024)), "c.txt")},
                   content_type="multipart/form-data", headers={"X-Clipper": "1"})
        self.assertEqual(r.status_code, 413)
        self.assertIn("1 MB", r.json["error"])

    def test_custom_header_required_against_csrf(self):
        app, c = self.app()
        self.assertEqual(self.upload(c, headers={}).status_code, 400)
        self.assertEqual(c.delete("/api/cookies").status_code, 400)

    def test_not_manageable_when_open_to_network_without_login(self):
        app, c = self.app(HOST="0.0.0.0")
        self.assertFalse(c.get("/api/cookies").json["can_manage"])
        r = self.upload(c)
        self.assertEqual(r.status_code, 403)
        self.assertIn("CLIPPER_AUTH_USER", r.json["error"])
        self.assertEqual(c.delete("/api/cookies", headers={"X-Clipper": "1"}).status_code, 403)
        app, c = self.app(HOST="0.0.0.0", AUTH_USER="a", AUTH_PASSWORD="b")  # dengan login: boleh
        import base64
        h = {"Authorization": "Basic " + base64.b64encode(b"a:b").decode(), "X-Clipper": "1"}
        self.assertEqual(self.upload(c, headers=h).status_code, 200)

    def test_cookie_endpoints_need_login_when_auth_enabled(self):
        app, c = self.app(AUTH_USER="a", AUTH_PASSWORD="b")
        self.assertEqual(c.get("/api/cookies").status_code, 401)
        self.assertEqual(self.upload(c).status_code, 401)

    def test_info_error_is_translated_with_payload(self):
        app, c = self.app()
        app.get_info = mock.Mock(side_effect=Exception(USER_ERROR))
        r = c.post("/api/info", json={"url": "https://www.youtube.com/watch?v=ty-bTl8iD7c"})
        j = r.json
        self.assertEqual((r.status_code, j["code"]), (400, "bot_check"))
        self.assertIn("anti-bot", j["error"])
        self.assertIn("Cookies", j["hint"])
        self.assertIn("not a bot", j["detail"])
        self.assertTrue(j["can_manage_cookies"])
        self.upload(c)  # setelah cookies ada, saran berubah
        self.assertIn("kedaluwarsa", c.post("/api/info", json={"url": "https://youtu.be/abcdefghijk"}).json["hint"])

    def test_job_error_is_translated(self):
        app, c = self.app()
        app.clip_auto = mock.Mock(side_effect=Exception(USER_ERROR))
        jid = c.post("/api/clip", json={"url": "https://youtu.be/abcdefghijk", "start": "0:10", "end": "0:20"}).json["job_id"]
        for _ in range(100):
            j = c.get(f"/api/job/{jid}").json
            if j["status"] == "error":
                break
            time.sleep(0.05)
        self.assertEqual((j["status"], j["error_code"]), ("error", "bot_check"))
        self.assertIn("anti-bot", j["error"])
        self.assertTrue(j["hint"] and j["detail"])

    def test_validation_errors_still_plain(self):
        app, c = self.app()
        r = c.post("/api/info", json={"url": "ftp://x"})
        self.assertEqual((r.status_code, r.json["code"]), (400, "other"))
        self.assertIn("http", r.json["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
