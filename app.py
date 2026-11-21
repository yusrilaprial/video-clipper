"""Web UI + API.  Lokal: python app.py   |   Server: gunicorn (lihat Dockerfile)."""
import hmac
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, render_template, request, send_file

import config
import render
from clipper import QUALITIES, clip_auto, get_info, resolve_look, resolve_range

CFG = config.get()  # dibaca dari .env / environment variable CLIPPER_*
OUTPUT = Path(CFG.output_dir)
OUTPUT.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_SLOTS = threading.BoundedSemaphore(CFG.max_jobs)  # batasi render bersamaan (ffmpeg berat)
JOB_TTL = 6 * 3600  # data job disimpan 6 jam di memori


# ------------------------------------------------------------------ keamanan
@app.before_request
def _auth():
    if request.path == "/healthz" or not CFG.auth_user:
        return None
    a = request.authorization
    if a and hmac.compare_digest(a.username or "", CFG.auth_user) and \
            hmac.compare_digest(a.password or "", CFG.auth_password or ""):
        return None
    return Response("Login diperlukan", 401, {"WWW-Authenticate": 'Basic realm="Video Clipper"'})


@app.after_request
def _headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


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


@app.get("/")
def index():
    return render_template(
        "index.html", qualities=list(QUALITIES), default_quality=CFG.quality, default_precise=CFG.precise,
        frames=["original", "tiktok", "reels", "shorts", "instagram", "square", "youtube", "twitter", "cinema"],
        default_frame=CFG.frame if CFG.frame in render.FRAME_PRESETS or CFG.frame == "original" else "custom",
        custom_frame=CFG.frame, default_fit=CFG.fit, default_subs=CFG.subs, default_sub_mode=CFG.sub_mode,
        default_sub_lang=CFG.sub_lang, max_clip=CFG.max_clip_seconds)


@app.post("/api/info")
def api_info():
    try:
        return jsonify(get_info(_check_url((request.json or {}).get("url"))))
    except Exception as e:  # noqa: BLE001
        return jsonify(error=str(e)), 400


def _run(job_id: str, p: dict) -> None:
    job = JOBS[job_id]
    job["status"], job["progress"] = "queued", "Menunggu antrean..."
    with _SLOTS:
        job.update(status="running", progress="Memulai...")

        def progress(d):
            if d.get("status") == "downloading":
                job["progress"] = d.get("_percent_str", "").strip()
            elif d.get("status") == "finished":
                mb = d.get("downloaded_bytes")
                job["progress"] = "Merender..." + (f" (diunduh {mb / 1048576:.1f} MB)" if mb else "")
            elif d.get("status") == "message":
                job["notes"].append({"level": d.get("level", "info"), "text": d.get("message", "")})
                if d.get("level") == "info":
                    job["progress"] = d.get("message", "")

        try:
            path = clip_auto(p["url"], p["start"], p["end"], OUTPUT, p["quality"], precise=p["precise"],
                             progress=progress, mode="auto", test=p["test"], look=p["look"])
            srt = path.with_suffix(".srt")
            job.update(status="done", file=str(path), name=path.name, srt=str(srt) if srt.exists() else None,
                       finished=time.time())
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
    public = {k: v for k, v in job.items() if k in ("status", "progress", "notes", "name", "error")}
    public["has_srt"] = bool(job.get("srt"))
    return jsonify(public)


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
    if CFG.host not in ("127.0.0.1", "localhost") and not CFG.auth_user:
        print("PERINGATAN: dapat diakses dari jaringan tanpa login. Isi CLIPPER_AUTH_USER dan CLIPPER_AUTH_PASSWORD.")
    app.run(host=CFG.host, port=CFG.port, debug=False, threaded=True)
