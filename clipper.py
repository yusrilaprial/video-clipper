"""Core logic: potong video dari URL (YouTube & platform lain yang didukung yt-dlp)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

import yt_dlp
from yt_dlp.utils import download_range_func

QUALITIES = {
    "best": None,
    "1080": 1080,
    "720": 720,
    "480": 480,
    "360": 360,
    "audio": "audio",  # hanya audio (mp3)
}

_TIME_RE = re.compile(r"^\d+(\.\d+)?$")


def parse_time(value: str | float | int) -> float:
    """Ubah '90', '1:30', '01:02:03', '1:30.5', '1h2m3s' menjadi detik."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    if not s:
        raise ValueError("Waktu kosong")

    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?", s)
    if m and any(m.groups()) and any(c in s for c in "hms"):
        h, mi, sec = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(sec or 0)

    parts = s.split(":")
    if len(parts) > 3 or not all(_TIME_RE.match(p) for p in parts):
        raise ValueError(f"Format waktu tidak valid: '{value}'")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return seconds


def format_time(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def get_info(url: str) -> dict:
    """Ambil metadata video (judul, durasi, thumbnail) tanpa mengunduh."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url", url),
        "live_status": info.get("live_status"),
    }


def _format_selector(quality: str) -> str:
    q = QUALITIES.get(quality, None)
    if q == "audio":
        return "bestaudio/best"
    h = f"[height<={q}]" if q else ""
    # Prioritaskan H.264 + AAC supaya hasil mp4 kompatibel di semua perangkat
    return (
        f"bv*[vcodec^=avc1]{h}+ba[ext=m4a]/"
        f"b[ext=mp4]{h}/"
        f"bv*{h}+ba/b{h}"
    )


def clip(
    url: str,
    start: str | float,
    end: str | float,
    output_dir: str | Path = "output",
    quality: str = "best",
    precise: bool = True,
    filename: Optional[str] = None,
    progress: Optional[Callable[[dict], None]] = None,
) -> Path:
    """Potong [start, end] dari url dan simpan. Return path file hasil.

    precise=True  -> potongan presisi ke detik (re-encode di titik potong, sedikit lebih lama).
    precise=False -> potong di keyframe terdekat (sangat cepat, bisa meleset beberapa detik).
    """
    t0, t1 = parse_time(start), parse_time(end)
    if t0 < 0 or t1 <= t0:
        raise ValueError("Waktu akhir harus lebih besar dari waktu mulai")
    if quality not in QUALITIES:
        raise ValueError(f"Kualitas tidak dikenal: {quality}. Pilih: {', '.join(QUALITIES)}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = filename or f"%(title).80s [{format_time(t0).replace(':', '-')} - {format_time(t1).replace(':', '-')}]"
    audio_only = QUALITIES[quality] == "audio"
    result: dict = {}

    def hook(d: dict) -> None:
        if d.get("status") == "finished":
            result["file"] = d.get("filename")
        if progress:
            progress(d)

    opts: dict = {
        "format": _format_selector(quality),
        "outtmpl": str(out_dir / f"{stem}.%(ext)s"),
        "download_ranges": download_range_func(None, [(t0, t1)]),
        "force_keyframes_at_cuts": precise,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": False,
        "progress_hooks": [hook],
        "postprocessor_hooks": [lambda d: progress and progress(d)],
    }
    if audio_only:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    else:
        opts["merge_output_format"] = "mp4"

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        final = ydl.prepare_filename(info)

    final_path = Path(final)
    if audio_only:
        final_path = final_path.with_suffix(".mp3")
    elif final_path.suffix != ".mp4":
        final_path = final_path.with_suffix(".mp4")
    if not final_path.exists() and result.get("file"):
        final_path = Path(result["file"])
    return final_path


def clip_auto(
    url: str,
    start: str | float,
    end: str | float,
    output_dir: str | Path = "output",
    quality: str = "best",
    precise: bool = True,
    filename: Optional[str] = None,
    progress: Optional[Callable[[dict], None]] = None,
    mode: str = "auto",
    test: bool = False,
) -> Path:
    """Pilih otomatis: livestream -> mode live (unduh segmen saja); selain itu -> mode biasa.

    mode: 'auto' | 'live' | 'vod'.  test=True -> hanya 10 detik pertama dari rentang (hemat kuota).
    """
    if mode not in ("auto", "live", "vod"):
        raise ValueError("mode harus auto, live, atau vod")
    if mode == "auto":
        status = get_info(url).get("live_status")
        if status == "is_upcoming":
            raise ValueError("Stream belum dimulai. Coba lagi setelah stream berjalan.")
        mode = "live" if status in ("is_live", "post_live") else "vod"
    if mode == "live":
        from live_clip import clip_live
        return clip_live(url, start, end, output_dir, quality, filename, progress, test=test)
    if test:
        end = min(parse_time(end), parse_time(start) + 10)
    return clip(url, start, end, output_dir, quality, precise, filename, progress)
