"""Web UI: python app.py  ->  buka http://127.0.0.1:5000"""
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from clipper import QUALITIES, clip_auto, get_info

app = Flask(__name__)
OUTPUT = Path("output")
JOBS: dict[str, dict] = {}


@app.get("/")
def index():
    return render_template("index.html", qualities=list(QUALITIES))


@app.post("/api/info")
def api_info():
    try:
        return jsonify(get_info(request.json["url"]))
    except Exception as e:  # noqa: BLE001
        return jsonify(error=str(e)), 400


def _run(job_id: str, p: dict) -> None:
    job = JOBS[job_id]

    def progress(d):
        if d.get("status") == "downloading":
            job["progress"] = d.get("_percent_str", "").strip()
        elif d.get("status") == "finished":
            mb = d.get("downloaded_bytes")
            job["progress"] = "Merender..." + (f" (diunduh {mb / 1048576:.1f} MB)" if mb else "")

    try:
        path = clip_auto(p["url"], p["start"], p["end"], OUTPUT, p["quality"],
                         precise=p["precise"], progress=progress, mode="auto", test=p["test"])
        job.update(status="done", file=str(path), name=path.name)
    except Exception as e:  # noqa: BLE001
        job.update(status="error", error=str(e))


@app.post("/api/clip")
def api_clip():
    d = request.json or {}
    try:
        from clipper import parse_time
        s, e = parse_time(d["start"]), parse_time(d["end"])
        if e <= s:
            raise ValueError("Waktu akhir harus lebih besar dari waktu mulai")
        if d.get("quality", "best") not in QUALITIES:
            raise ValueError("Kualitas tidak valid")
    except (KeyError, ValueError) as ex:
        return jsonify(error=str(ex)), 400
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"status": "running", "progress": "Memulai..."}
    params = dict(url=d["url"], start=d["start"], end=d["end"],
                  quality=d.get("quality", "best"), precise=bool(d.get("precise", True)),
                  test=bool(d.get("test", False)))
    threading.Thread(target=_run, args=(job_id, params), daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/job/<job_id>")
def api_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Job tidak ditemukan"), 404
    return jsonify({k: v for k, v in job.items() if k != "file"})


@app.get("/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return "Belum siap", 404
    return send_file(job["file"], as_attachment=True, download_name=job["name"])


if __name__ == "__main__":
    OUTPUT.mkdir(exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=False)
