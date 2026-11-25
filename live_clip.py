"""Mode LIVE: klip bagian lampau dari livestream YouTube yang masih berjalan,
dengan hanya mengunduh segmen (sq=N) yang dibutuhkan, bukan seluruh stream.

Cara kerja:
  1. yt-dlp (live_from_start) dipakai hanya untuk mengambil daftar format + URL dasar segmen.
  2. Waktu mulai/akhir dipetakan ke nomor segmen: sq = detik / target_duration.
  3. Hanya segmen itu (plus header init sq=0 dan 1 segmen margin di tiap sisi) yang diunduh.
  4. ffmpeg memotong tepat di waktu yang diminta dan merender ke MP4 (atau MP3).
"""
from __future__ import annotations

import http.client
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import yt_dlp
from yt_dlp.utils import sanitize_filename, update_url_query

import config
import render
import subtitles
from clipper import QUALITIES, format_time, resolve_look, resolve_range, ydl_session


class LiveClipError(Exception):
    """Kesalahan yang aman ditampilkan ke pengguna."""


# --------------------------------------------------------------------------- HTTP
def _headers(fmt: dict) -> dict:
    h = dict(fmt.get("http_headers") or {})
    h.pop("Accept-Encoding", None)  # urllib tidak mendekompresi otomatis
    h.setdefault("User-Agent", "Mozilla/5.0")
    return h


def _opener() -> urllib.request.OpenerDirector:
    proxy = config.get().proxy
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def _request(url: str, headers: dict, method: str = "GET", retries: Optional[int] = None,
             timeout: Optional[int] = None):
    cfg = config.get()
    retries = cfg.live_retries if retries is None else retries
    timeout = cfg.http_timeout if timeout is None else timeout
    opener = _opener()
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers, method=method)
            with opener.open(req, timeout=timeout) as r:
                body = r.read() if method == "GET" else b""
                return body, r.headers
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise LiveClipError(
                    f"Segmen tidak tersedia (HTTP {e.code}). Kemungkinan waktu yang diminta di luar "
                    "jendela DVR, atau stream ini tidak mengaktifkan DVR."
                ) from e
            last = e
            if e.code == 403 and attempt >= 1:
                raise LiveClipError(
                    "Akses ditolak (HTTP 403). URL mungkin kedaluwarsa atau YouTube meminta login/PO token; "
                    "coba lagi atau gunakan cookies browser."
                ) from e
        except (OSError, http.client.HTTPException) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise LiveClipError(f"Gagal mengunduh segmen setelah {retries} percobaan: {last}")


def head_seq(base_url: str, headers: dict) -> int:
    _, h = _request(base_url, headers, method="HEAD")
    v = h.get("X-Head-Seqnum")
    if v is None:
        raise LiveClipError("Tidak bisa membaca posisi live edge (header X-Head-Seqnum tidak ada).")
    return int(v)


class _Tracker:
    def __init__(self, cb: Optional[Callable[[dict], None]], total: int):
        self.cb, self.total = cb, max(total, 1)
        self.done = 0
        self.bytes = 0
        self.t0 = time.time()
        self.lock = threading.Lock()

    def tick(self, nbytes: int) -> None:
        with self.lock:
            self.done += 1
            self.bytes += nbytes
            if self.cb:
                el = max(time.time() - self.t0, 1e-3)
                self.cb({
                    "status": "downloading",
                    "_percent_str": f"{min(self.done / self.total, 1) * 100:.1f}%",
                    "_speed_str": f"{self.bytes / el / 1048576:.2f}MiB/s",
                    "downloaded_bytes": self.bytes,
                })


# --------------------------------------------------------------------------- init segment
def _init_part(seg0: bytes) -> bytes:
    """Ambil bagian header/init dari segmen 0 (tanpa data media)."""
    if seg0[:4] == b"\x1a\x45\xdf\xa3":  # WebM/EBML: header = semua sebelum Cluster pertama
        idx = seg0.find(b"\x1f\x43\xb6\x75")
        if idx <= 0:
            raise LiveClipError("Header WebM tidak ditemukan pada segmen 0")
        return seg0[:idx]
    pos, n = 0, len(seg0)  # MP4: ftyp + moov (sebelum moof/sidx/styp/mdat)
    while pos + 8 <= n:
        size = int.from_bytes(seg0[pos:pos + 4], "big")
        typ = seg0[pos + 4:pos + 8]
        if typ in (b"moof", b"sidx", b"styp", b"mdat"):
            break
        if size == 1:
            size = int.from_bytes(seg0[pos + 8:pos + 16], "big")
        elif size == 0:
            size = n - pos
        if size < 8:
            break
        pos += size
    init = seg0[:pos]
    if b"moov" not in init:
        raise LiveClipError("Header init MP4 (moov) tidak ditemukan pada segmen 0")
    return init


# --------------------------------------------------------------------------- planning
def _head(fmt: dict) -> dict:
    headers = _headers(fmt)
    return {"base": fmt["url"], "headers": headers, "d": float(fmt["target_duration"]),
            "last": head_seq(fmt["url"], headers)}


def _plan(h: dict, t0: float, t1: float, margin: Optional[int] = None) -> dict:
    margin = config.get().live_margin if margin is None else margin
    d, last = h["d"], h["last"]
    safe_last = last - 1  # segmen paling ujung bisa belum lengkap
    stream_len = last * d
    a_core = int(t0 // d)
    b_core = int((t1 - 1e-6) // d)
    if b_core > safe_last:
        raise LiveClipError(
            f"Waktu akhir {format_time(t1)} melewati live edge. "
            f"Panjang stream saat ini sekitar {format_time(stream_len)}."
        )
    return {
        "base": h["base"], "headers": h["headers"], "d": d,
        "a": max(0, a_core - margin), "b": min(b_core + margin, safe_last),
        "stream_len": stream_len,
    }


def _fetch_stream(p: dict, dest: Path, tracker: _Tracker, workers: int) -> None:
    def get(sq: int) -> bytes:
        body, _ = _request(update_url_query(p["base"], {"sq": str(sq)}), p["headers"])
        tracker.tick(len(body))
        return body

    with open(dest, "wb") as out:
        if p["a"] >= 1:  # perlu header init dari sq=0
            out.write(_init_part(get(0)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for data in ex.map(get, range(p["a"], p["b"] + 1)):
                out.write(data)


def _count(p: dict) -> int:
    return (p["b"] - p["a"] + 1) + (1 if p["a"] >= 1 else 0)


def _first_pts(path: Path, stream: str) -> Optional[float]:
    cmd = [config.get().ffprobe, "-v", "error", "-select_streams", stream, "-read_intervals", "%+#60",
           "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
    except FileNotFoundError:
        raise LiveClipError(f"ffprobe tidak ditemukan ({cmd[0]}). Pasang ffmpeg atau atur CLIPPER_FFPROBE_PATH / "
                            "CLIPPER_FFMPEG_PATH.") from None
    vals = []
    for tok in out.split():
        try:
            vals.append(float(tok.strip(",")))
        except ValueError:
            pass
    return min(vals) if vals else None


# --------------------------------------------------------------------------- format selection
def _pick(fmts: list[dict], quality: str):
    q = QUALITIES[quality]
    has_v = lambda f: f.get("vcodec") not in (None, "none")  # noqa: E731
    has_a = lambda f: f.get("acodec") not in (None, "none")  # noqa: E731
    vids = [f for f in fmts if has_v(f) and not has_a(f)]
    auds = [f for f in fmts if has_a(f) and not has_v(f)]
    if not auds:
        raise LiveClipError("Format audio live tidak ditemukan.")
    best_a = max(auds, key=lambda f: (f.get("language_preference") or 0,
                                      str(f.get("acodec", "")).startswith("mp4a"),
                                      f.get("abr") or f.get("tbr") or 0))
    if q == "audio":
        return None, best_a
    if not vids:
        raise LiveClipError("Format video live tidak ditemukan.")
    if isinstance(q, int):
        cand = [f for f in vids if (f.get("height") or 0) <= q] or [min(vids, key=lambda f: f.get("height") or 0)]
    else:
        cand = vids
    best_v = max(cand, key=lambda f: (str(f.get("vcodec", "")).startswith("avc1"),
                                      f.get("height") or 0, f.get("tbr") or 0))
    return best_v, best_a


def extract_live(url: str) -> tuple[dict, list[dict]]:
    extra = {"skip_download": True, "live_from_start": True}
    try:
        with ydl_session(extra) as opts, yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        raise LiveClipError(f"Gagal membaca info live: {e}") from e
    if info.get("live_status") not in ("is_live", "post_live"):
        raise LiveClipError("Video ini bukan livestream yang sedang berjalan. Gunakan mode biasa (--mode vod).")
    fmts = [f for f in info.get("formats", []) if f.get("target_duration") and f.get("url")]
    if not fmts:
        raise LiveClipError(
            "Format segmen live tidak tersedia (stream tanpa DVR, atau YouTube memblokir/meminta "
            "login/PO token). Coba perbarui yt-dlp atau gunakan cookies browser."
        )
    return info, fmts


# --------------------------------------------------------------------------- main
def clip_from_formats(
    title: str, vfmt: Optional[dict], afmt: dict, start, end,
    output_dir=None, filename: Optional[str] = None,
    progress: Optional[Callable[[dict], None]] = None, workers: Optional[int] = None, test: bool = False,
    look: Optional[dict] = None, info: Optional[dict] = None,
) -> Path:
    """start/end: absolut (detik dari awal stream, mis. '1:20:00') atau relatif ke live edge
    ('-1:30' = 1 mnt 30 dtk lalu, '0:00' = sekarang)."""
    cfg = config.get()
    workers = cfg.live_workers if workers is None else workers
    output_dir = cfg.output_dir if output_dir is None else output_dir
    t0, t1, relative = resolve_range(start, end)
    audio_only = vfmt is None
    look = resolve_look(look)
    size = None if audio_only else render.resolve_frame(look["frame"])
    sub_mode = "srt" if audio_only else look["sub_mode"]

    heads = {"a": _head(afmt)}
    if vfmt:
        heads["v"] = _head(vfmt)
    edge = min(h["last"] * h["d"] for h in heads.values())  # akhir segmen lengkap terakhir = "sekarang"
    if relative:
        t0, t1 = edge + t0, edge + t1
        if t0 < 0:
            raise LiveClipError(f"Waktu mulai melewati awal stream. Panjang stream saat ini sekitar {format_time(edge)}.")
    if test:
        t1 = min(t1, t0 + cfg.test_seconds)
    length = t1 - t0

    plans = {k: _plan(h, t0, t1) for k, h in heads.items()}

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = filename or sanitize_filename(
        f"{title[:cfg.title_max]} [live {format_time(t0).replace(':', '-')} - {format_time(t1).replace(':', '-')}]")
    tag = f" [{render.frame_tag(look['frame'])}]" if size else ""
    out_path = (out_dir / f"{name}{tag}.{'mp3' if audio_only else 'mp4'}").resolve()

    tracker = _Tracker(progress, sum(_count(p) for p in plans.values()))
    with tempfile.TemporaryDirectory(prefix="vclip_") as tmp_s:
        tmp = Path(tmp_s)
        files = {k: tmp / f"{k}.bin" for k in plans}
        for k, p in plans.items():
            _fetch_stream(p, files[k], tracker, workers)
        if progress:
            progress({"status": "finished", "filename": str(out_path), "downloaded_bytes": tracker.bytes})

        ref = "a" if audio_only else "v"
        p = plans[ref]
        first = _first_pts(files[ref], "a:0" if audio_only else "v:0")
        nominal = p["a"] * p["d"]
        if first is not None and abs(first - nominal) >= p["d"]:
            # timeline stream bergeser dari 0 -> hitung relatif terhadap segmen pertama yang diunduh
            T0 = first + (t0 - nominal)
        else:
            # timeline mulai dari 0 (umum): waktu absolut, kebal terhadap selisih panjang segmen
            T0 = t0
        T1 = T0 + length  # video & audio memakai timeline yang sama

        cues: list = []
        if look["subs"] != "off":
            cues = subtitles.collect_cues(
                look["subs"], look["sub_lang"], info, t0, t1,
                lambda: render.extract_wav(files["a"], tmp / "a.wav", T0, T1), progress)
        src_wh = None if audio_only else render.probe_size(files["v"])
        out_wh = size or src_wh or (1080, 1920)
        cues = render.prepare_cues(cues, out_wh, cfg.sub_scale) if cues else []
        if cues and sub_mode == "srt":
            out_path.with_suffix(".srt").write_text(subtitles.to_srt(cues), encoding="utf-8")
        burn = bool(cues) and sub_mode == "burn"
        soft = bool(cues) and sub_mode == "soft"
        if burn:
            (tmp / "subs.ass").write_text(render.to_ass(cues, out_wh, cfg.sub_scale, cfg.sub_font), encoding="utf-8")

        cmd = [cfg.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-copyts"]
        fc, maps = [], []
        n_in = 0
        if vfmt:
            cmd += ["-i", str(files["v"])]
            fc.append(f"[0:v:0]trim=start={T0:.3f}:end={T1:.3f},setpts=PTS-STARTPTS[vtrim]")
            fc.append(render.video_graph("vtrim", "v", size, look["fit"], look["focus"],
                                         "subs.ass" if burn else None))
            maps += ["-map", "[v]"]
            n_in += 1
        cmd += ["-i", str(files["a"])]
        fc.append(f"[{n_in}:a:0]atrim=start={T0:.3f}:end={T1:.3f},asetpts=PTS-STARTPTS[a]")
        maps += ["-map", "[a]"]
        n_in += 1
        if soft:
            (tmp / "subs.srt").write_text(subtitles.to_srt(cues), encoding="utf-8")
            cmd += ["-i", str(tmp / "subs.srt")]
            maps += ["-map", f"{n_in}:0"]
        cmd += ["-filter_complex", ";".join(fc)] + maps
        cmd += render.encode_args(audio_only)
        if soft:
            cmd += ["-c:s", "mov_text"]
        cmd.append(str(out_path))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp)
        except FileNotFoundError:
            raise LiveClipError(f"ffmpeg tidak ditemukan ({cfg.ffmpeg}). Pasang ffmpeg atau atur "
                                "CLIPPER_FFMPEG_PATH.") from None
        if r.returncode != 0:
            raise LiveClipError(f"ffmpeg gagal: {r.stderr.strip()[-500:]}")
    return out_path


def clip_live(
    url: str, start, end, output_dir=None, quality: Optional[str] = None,
    filename: Optional[str] = None, progress: Optional[Callable[[dict], None]] = None,
    test: bool = False, workers: Optional[int] = None, look: Optional[dict] = None,
) -> Path:
    quality = config.get().quality if quality is None else quality
    resolve_range(start, end)  # validasi cepat sebelum menghubungi YouTube
    if quality not in QUALITIES:
        raise ValueError(f"Kualitas tidak dikenal: {quality}")
    info, fmts = extract_live(url)
    vfmt, afmt = _pick(fmts, quality)
    look = resolve_look(look)  # validasi sebelum mengunduh
    return clip_from_formats(info.get("title") or "live", vfmt, afmt, start, end,
                             output_dir, filename, progress, workers, test, look, info)
