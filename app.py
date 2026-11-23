"""Web UI + API.  Lokal: python app.py   |   Server: gunicorn (lihat Dockerfile)."""
import hashlib
import hmac
import mimetypes
import re
import secrets
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import render
from clipper import QUALITIES, clip_auto, get_info, resolve_look, resolve_range

CFG = config.get()  # dibaca dari .env / environment variable CLIPPER_*
OUTPUT = Path(CFG.output_dir)
OUTPUT.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
AUTH_ON = bool(CFG.auth_user)
# Kunci sesi: dari CLIPPER_SECRET_KEY, atau diturunkan dari kredensial (stabil saat restart; ganti password = semua sesi keluar)
_FINGERPRINT = hashlib.sha256(f"clipper-auth:{CFG.auth_user}:{CFG.auth_password}".encode()).hexdigest()[:20]
app.secret_key = CFG.secret_key or hashlib.sha256(f"clipper-session-key:{CFG.auth_user}:{CFG.auth_password}".encode()).hexdigest()
app.config.update(
    SESSION_COOKIE_NAME="clipper_session", SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=CFG.cookie_secure, PERMANENT_SESSION_LIFETIME=timedelta(days=14),
)
if CFG.trust_proxy:  # di belakang reverse proxy: pakai IP/skema asli dari X-Forwarded-*
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_SLOTS = threading.BoundedSemaphore(CFG.max_jobs)  # batasi render bersamaan (ffmpeg berat)
JOB_TTL = 6 * 3600  # data job disimpan 6 jam di memori


# ------------------------------------------------------------------ keamanan & login
def _csrf() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def _csrf_ok() -> bool:
    sent = request.form.get("csrf", "")
    return bool(sent) and hmac.compare_digest(sent, session.get("csrf", ""))


def _safe_next(target: str) -> str:
    """Hanya path internal (cegah open redirect)."""
    t = (target or "").strip()
    if t.startswith("/") and not t.startswith("//") and "\\" not in t and "://" not in t and t.isprintable():
        return t
    return "/"


def _session_ok() -> bool:
    return hmac.compare_digest(str(session.get("auth", "")), _FINGERPRINT)


def _basic_ok() -> bool:  # untuk skrip/curl; browser memakai halaman login
    a = request.authorization
    if not a:
        return False
    u = hmac.compare_digest((a.username or "").encode(), (CFG.auth_user or "").encode())
    p = hmac.compare_digest((a.password or "").encode(), (CFG.auth_password or "").encode())
    return u and p


_FAILS: dict[str, dict] = {}
_FAILS_LOCK = threading.Lock()


def _lock_remaining(ip: str) -> int:
    with _FAILS_LOCK:
        st = _FAILS.get(ip)
        return max(0, int(st["locked_until"] - time.time())) if st else 0


def _register_failure(ip: str) -> None:
    now = time.time()
    with _FAILS_LOCK:
        for k in [k for k, v in _FAILS.items() if v["locked_until"] < now and now - v["first"] > 900]:
            _FAILS.pop(k, None)
        st = _FAILS.setdefault(ip, {"n": 0, "first": now, "locked_until": 0.0})
        if now - st["first"] > 900:  # jendela hitung 15 menit
            st.update(n=0, first=now)
        st["n"] += 1
        if st["n"] >= CFG.login_max_attempts:
            st.update(n=0, first=now, locked_until=now + CFG.login_lock_seconds)


@app.before_request
def _auth():
    if not AUTH_ON or request.path in ("/healthz", "/login"):
        return None
    if _session_ok() or _basic_ok():
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Sesi berakhir atau belum login", login=url_for("login")), 401
    return redirect(url_for("login", next=_safe_next(request.full_path.rstrip("?"))))


@app.after_request
def _headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Cache-Control", "no-store")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
    return resp


def _render_login(error=None, status=200, username="", retry_after=0):
    html = render_template("login.html", error=error, username=username, csrf=_csrf(), retry_after=retry_after,
                           next=_safe_next(request.values.get("next", "")), bye=request.args.get("bye") == "1")
    return html, status


@app.route("/login", methods=["GET", "POST"])
def login():
    if not AUTH_ON:
        return redirect("/")
    if request.method == "GET":
        if _session_ok():
            return redirect(_safe_next(request.args.get("next", "")))
        return _render_login()
    ip = request.remote_addr or "?"
    username = (request.form.get("username") or "").strip()
    if not _csrf_ok():
        return _render_login("Halaman login kedaluwarsa. Silakan coba lagi.", 400, username)
    wait = _lock_remaining(ip)
    if wait:
        return _render_login(f"Terlalu banyak percobaan gagal. Coba lagi dalam {wait} detik.", 429, username, wait)
    u = hmac.compare_digest(username.encode(), (CFG.auth_user or "").encode())
    p = hmac.compare_digest((request.form.get("password") or "").encode(), (CFG.auth_password or "").encode())
    if not (u and p):
        _register_failure(ip)
        time.sleep(0.4)  # perlambat tebak-tebakan password
        wait = _lock_remaining(ip)
        msg = (f"Terlalu banyak percobaan gagal. Coba lagi dalam {wait} detik." if wait
               else "Nama pengguna atau kata sandi salah.")
        return _render_login(msg, 429 if wait else 401, username, wait)
    with _FAILS_LOCK:
        _FAILS.pop(ip, None)
    session.clear()  # sesi baru (cegah session fixation)
    session["auth"] = _FINGERPRINT
    session.permanent = request.form.get("remember") == "1"
    _csrf()
    return redirect(_safe_next(request.form.get("next", "")))


@app.post("/logout")
def logout():
    if not _csrf_ok():
        return redirect("/")
    session.clear()
    return redirect(url_for("login", bye=1))


def _check_url(url: str) -> str:
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Link harus diawali http:// atau https://")
    host = u.hostname.lower()
    if CFG.allowed_domains and not any(host == d or host.endswith("." + d) for d in CFG.allowed_domains):
        raise ValueError("Domain tidak diizinkan di server ini. Diizinkan: " + ", ".join(CFG.allowed_domains))
    return u.geturl()


# ------------------------------------------------------------------ halaman & API
@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


_FRAME_UI = [  # (id, label, rasio) untuk kartu pilihan frame
    ("original", "Asli", None), ("tiktok", "TikTok", "9:16"), ("reels", "Reels", "9:16"), ("shorts", "Shorts", "9:16"),
    ("instagram", "IG Feed", "4:5"), ("square", "Persegi", "1:1"), ("youtube", "YouTube", "16:9"),
    ("twitter", "X / Twitter", "16:9"), ("cinema", "Sinema", "21:9"),
]
_QUALITY_LABELS = {"best": "Terbaik", "1080": "1080p", "720": "720p", "480": "480p", "360": "360p", "audio": "Audio (MP3)"}


def _frames() -> list[dict]:
    out = []
    for fid, label, ratio in _FRAME_UI:
        wh = render.FRAME_PRESETS.get(fid)
        if wh:
            w, h = wh
            k = 34 / max(w, h)
            out.append(dict(id=fid, label=label, ratio=ratio, w=w, h=h, sw=round(w * k), sh=round(h * k)))
        else:
            out.append(dict(id=fid, label=label, ratio="Ukuran asli", w=None, h=None, sw=34, sh=22))
    return out


@app.get("/")
def index():
    known = {f[0] for f in _FRAME_UI}
    custom = CFG.frame if CFG.frame not in known else ""
    defaults = dict(
        frame="custom" if custom else CFG.frame, custom_frame=custom, fit=CFG.fit, focus=CFG.focus, subs=CFG.subs,
        sub_lang=CFG.sub_lang, sub_mode=CFG.sub_mode, quality=CFG.quality, precise=CFG.precise,
        max_clip=CFG.max_clip_seconds, test_seconds=CFG.test_seconds, auth=AUTH_ON)
    frames = _frames()
    return render_template(
        "index.html", frames=frames, frames_json={f["id"]: f for f in frames},
        qualities=[(q, _QUALITY_LABELS[q]) for q in QUALITIES], defaults=defaults, auth_enabled=AUTH_ON, csrf=_csrf(),
        max_clip=CFG.max_clip_seconds)


@app.post("/api/info")
def api_info():
    try:
        return jsonify(get_info(_check_url((request.json or {}).get("url"))))
    except Exception as e:  # noqa: BLE001
        return jsonify(error=str(e)), 400


def _percent(d: dict):
    """Persen unduhan dari dict progress yt-dlp / unduhan segmen (None bila tidak diketahui)."""
    done, total = d.get("downloaded_bytes"), d.get("total_bytes") or d.get("total_bytes_estimate")
    if done and total:
        return max(0.0, min(100.0, done * 100 / total))
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", d.get("_percent_str", "") or "")
    return float(m.group(1)) if m else None


def _run(job_id: str, p: dict) -> None:
    job = JOBS[job_id]
    job.update(status="queued", stage="queued", progress="Menunggu antrean...")
    with _SLOTS:
        job.update(status="running", stage="download", percent=None, progress="Memulai...")

        def progress(d):
            if d.get("status") == "downloading":
                job["stage"], job["percent"] = "download", _percent(d)
                job["progress"] = f"Mengunduh {job['percent']:.0f}%" if job["percent"] is not None else "Mengunduh..."
            elif d.get("status") == "finished":
                mb = d.get("downloaded_bytes")
                job["stage"], job["percent"] = "process", None
                if mb:
                    job["downloaded_mb"] = round(mb / 1048576, 1)
                job["progress"] = "Memproses (subtitle & render)..."
            elif d.get("status") == "message":
                job["notes"].append({"level": d.get("level", "info"), "text": d.get("message", "")})
                if d.get("level") == "info":
                    job["progress"] = d.get("message", "")

        try:
            path = clip_auto(p["url"], p["start"], p["end"], OUTPUT, p["quality"], precise=p["precise"],
                             progress=progress, mode="auto", test=p["test"], look=p["look"])
            srt = path.with_suffix(".srt")
            job.update(status="done", stage="done", percent=100.0, file=str(path), name=path.name,
                       size=path.stat().st_size, kind="audio" if path.suffix.lower() == ".mp3" else "video",
                       srt=str(srt) if srt.exists() else None, finished=time.time())
        except Exception as e:  # noqa: BLE001
            job.update(status="error", error=str(e), finished=time.time())


@app.post("/api/clip")
def api_clip():
    d = request.json or {}
    try:
        url = _check_url(d.get("url"))
        t0, t1, _ = resolve_range(d.get("start", ""), d.get("end", ""))
        quality = d.get("quality") or CFG.quality
        if quality not in QUALITIES:
            raise ValueError("Kualitas tidak valid")
        look = {k: (d.get(k) if d.get(k) not in ("", None) else None)
                for k in ("frame", "fit", "focus", "subs", "sub_lang", "sub_mode")}
        resolve_look(look)
        test = bool(d.get("test", False))
        if CFG.max_clip_seconds and not test and (t1 - t0) > CFG.max_clip_seconds:
            raise ValueError(f"Durasi klip maksimal {CFG.max_clip_seconds // 60} menit {CFG.max_clip_seconds % 60} detik "
                             "di server ini")
    except (KeyError, ValueError) as ex:
        return jsonify(error=str(ex)), 400
    _gc()
    job_id = uuid.uuid4().hex[:16]
    JOBS[job_id] = {"status": "queued", "progress": "Menunggu antrean...", "notes": [], "created": time.time()}
    params = dict(url=url, start=d["start"], end=d["end"], quality=quality, test=test, look=look,
                  precise=bool(d.get("precise", CFG.precise)))
    threading.Thread(target=_run, args=(job_id, params), daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/job/<job_id>")
def api_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Job tidak ditemukan"), 404
    public = {k: v for k, v in job.items()
              if k in ("status", "stage", "percent", "progress", "notes", "name", "error", "size", "kind", "downloaded_mb")}
    public["has_srt"] = bool(job.get("srt"))
    return jsonify(public)


@app.get("/media/<job_id>")
def media(job_id):
    """Putar hasil langsung di browser (mendukung Range agar bisa di-seek)."""
    job = JOBS.get(job_id)
    path = job.get("file") if job and job.get("status") == "done" else None
    if not path or not Path(path).is_file():
        return "File tidak ditemukan (mungkin sudah dibersihkan)", 404
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return send_file(path, mimetype=mime, conditional=True, as_attachment=False)


@app.get("/download/<job_id>")
@app.get("/download/<job_id>/<kind>")
def download(job_id, kind="video"):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return "Belum siap", 404
    path = job.get("srt") if kind == "srt" else job.get("file")
    if not path or not Path(path).is_file():
        return "File tidak ditemukan (mungkin sudah dibersihkan)", 404
    return send_file(path, as_attachment=True, download_name=Path(path).name)


# ------------------------------------------------------------------ pembersihan
def _gc() -> None:
    now = time.time()
    with _JOBS_LOCK:
        for jid in [j for j, v in JOBS.items() if now - v.get("created", now) > JOB_TTL
                    and v.get("status") in ("done", "error")]:
            JOBS.pop(jid, None)


def _cleanup_loop() -> None:
    limit = CFG.retention_hours * 3600
    while True:
        cutoff = time.time() - limit
        for f in OUTPUT.glob("*"):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
        time.sleep(600)


if CFG.retention_hours > 0:
    threading.Thread(target=_cleanup_loop, daemon=True).start()


if __name__ == "__main__":
    print(f"Web UI: http://{CFG.host}:{CFG.port}  (hasil di folder: {OUTPUT})")
    if CFG.host not in ("127.0.0.1", "localhost") and not AUTH_ON:
        print("PERINGATAN: dapat diakses dari jaringan tanpa login. Isi CLIPPER_AUTH_USER dan CLIPPER_AUTH_PASSWORD.")
    app.run(host=CFG.host, port=CFG.port, debug=False, threaded=True)
