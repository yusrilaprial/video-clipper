"""Core logic: potong video dari URL (YouTube & platform lain yang didukung yt-dlp)."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Callable, Optional

import yt_dlp

import config
import render
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


def is_relative(value) -> bool:
    """True untuk waktu relatif terhadap live edge: '-1:30', '-90', 'now'."""
    s = str(value).strip().lower()
    return s.startswith("-") or s in ("now", "live", "sekarang")


def parse_time(value: str | float | int) -> float:
    """Ubah '90', '1:30', '01:02:03', '1:30.5', '1h2m3s' menjadi detik.
    Awalan '-' menghasilkan nilai negatif (relatif ke live edge); 'now' = 0."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    if not s:
        raise ValueError("Waktu kosong")
    if s in ("now", "live", "sekarang"):
        return 0.0
    if s.startswith("-"):
        return -parse_time(s[1:])

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


def resolve_range(start, end) -> tuple[float, float, bool]:
    """Return (t0, t1, relative). Jika relatif, t0/t1 adalah offset (<= 0) dari live edge; 0 = sekarang."""
    rs, re_ = is_relative(start), is_relative(end)
    t0, t1 = parse_time(start), parse_time(end)
    relative = rs or re_
    if relative and ((not rs and t0 != 0) or (not re_ and t1 != 0)):
        raise ValueError("Pakai waktu relatif untuk keduanya (mis. mulai -1:30, akhir 0:00), "
                         "atau waktu absolut untuk keduanya.")
    if relative and (t0 > 0 or t1 > 0):
        raise ValueError("Waktu relatif harus negatif atau 0 (mis. -1:30).")
    if t1 <= t0:
        raise ValueError("Waktu akhir harus lebih besar dari waktu mulai")
    if not relative and t0 < 0:
        raise ValueError("Waktu mulai tidak boleh negatif")
    return t0, t1, relative


def format_time(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def resolve_look(look: Optional[dict] = None) -> dict:
    """Pengaturan tampilan klip (frame, fit, subtitle). Nilai kosong memakai konfigurasi .env."""
    cfg = config.get()
    look = look or {}
    out = {
        "frame": (look.get("frame") or cfg.frame).strip().lower(),
        "fit": (look.get("fit") or cfg.fit).lower(),
        "focus": cfg.focus if look.get("focus") is None else float(look["focus"]),
        "subs": (look.get("subs") or cfg.subs).lower(),
        "sub_lang": (look.get("sub_lang") or cfg.sub_lang).strip().lower(),
        "sub_mode": (look.get("sub_mode") or cfg.sub_mode).lower(),
    }
    if out["fit"] not in config.VALID_FITS:
        raise ValueError(f"Fit tidak dikenal: {out['fit']}. Pilih: {', '.join(config.VALID_FITS)}")
    if out["subs"] not in config.VALID_SUBS:
        raise ValueError(f"Mode subtitle tidak dikenal: {out['subs']}. Pilih: {', '.join(config.VALID_SUBS)}")
    if out["sub_mode"] not in config.VALID_SUB_MODES:
        raise ValueError(f"Jenis subtitle tidak dikenal: {out['sub_mode']}. Pilih: {', '.join(config.VALID_SUB_MODES)}")
    if not 0 <= out["focus"] <= 1:
        raise ValueError("Focus harus antara 0 dan 1")
    if not re.fullmatch(r"auto|[a-z]{2,3}(-[a-z0-9]{2,8})?", out["sub_lang"]):
        raise ValueError(f"Kode bahasa subtitle tidak valid: {out['sub_lang']} (contoh: auto, id, en, ja)")
    try:
        render.resolve_frame(out["frame"])
    except ValueError as e:
        raise ValueError(f"Frame '{out['frame']}' {e}") from None
    return out


def ydl_base_opts() -> dict:
    """Opsi yt-dlp bersama (proxy, cookies, lokasi ffmpeg) dari konfigurasi/.env."""
    c = config.get()
    o: dict = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if c.proxy:
        o["proxy"] = c.proxy
    if c.cookies_file:
        o["cookiefile"] = c.cookies_file
    elif c.cookies_from_browser:
        o["cookiesfrombrowser"] = tuple(c.cookies_from_browser.split(":"))
    if c.ffmpeg_location:
        o["ffmpeg_location"] = c.ffmpeg_location
    return o


def get_info(url: str) -> dict:
    """Ambil metadata video (judul, durasi, thumbnail) tanpa mengunduh."""
    opts = {**ydl_base_opts(), "skip_download": True}
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
    output_dir: str | Path | None = None,
    quality: Optional[str] = None,
    precise: Optional[bool] = None,
    filename: Optional[str] = None,
    progress: Optional[Callable[[dict], None]] = None,
    look: Optional[dict] = None,
) -> Path:
    """Potong [start, end] dari url dan simpan. Return path file hasil.
    look: {frame, fit, focus, subs, sub_lang, sub_mode} (None = dari konfigurasi).
    Nilai None memakai konfigurasi (.env / environment variable CLIPPER_*).

    precise=True  -> potongan presisi ke detik (re-encode di titik potong, sedikit lebih lama).
    precise=False -> potong di keyframe terdekat (sangat cepat, bisa meleset beberapa detik).
    """
    cfg = config.get()
    output_dir = cfg.output_dir if output_dir is None else output_dir
    quality = cfg.quality if quality is None else quality
    precise = cfg.precise if precise is None else precise
    if is_relative(start) or is_relative(end):
        raise ValueError("Waktu relatif (-mm:ss / now) hanya untuk livestream yang sedang berjalan.")
    t0, t1, _ = resolve_range(start, end)
    if quality not in QUALITIES:
        raise ValueError(f"Kualitas tidak dikenal: {quality}. Pilih: {', '.join(QUALITIES)}")

    look = resolve_look(look)
    audio_only = QUALITIES[quality] == "audio"
    size = None if audio_only else render.resolve_frame(look["frame"])
    post = bool(size) or look["subs"] != "off"  # perlu render akhir (frame / subtitle)
    if look["subs"] != "off":
        precise = True  # subtitle butuh titik potong presisi agar waktunya sinkron

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = tempfile.TemporaryDirectory(prefix="vclip_") if post else None
    dl_dir = Path(work.name) if work else out_dir

    stem = filename or f"%(title).{cfg.title_max}s [{format_time(t0).replace(':', '-')} - {format_time(t1).replace(':', '-')}]"
    result: dict = {}

    def hook(d: dict) -> None:
        if d.get("status") == "finished":
            result["file"] = d.get("filename")
        if progress:
            progress(d)

    opts: dict = {
        **ydl_base_opts(),
        "format": _format_selector(quality),
        "outtmpl": str(dl_dir / f"{stem}.%(ext)s"),
        "download_ranges": download_range_func(None, [(t0, t1)]),
        "force_keyframes_at_cuts": precise,
        "restrictfilenames": False,
        "progress_hooks": [hook],
        "postprocessor_hooks": [lambda d: progress and progress(d)],
    }
    if audio_only:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
             "preferredquality": cfg.audio_bitrate.rstrip("k")}
        ]
    else:
        opts["merge_output_format"] = "mp4"

    try:
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
        if post:
            final_path = render.postprocess_vod(
                final_path, info, t0, t1, out_dir, frame=look["frame"], fit=look["fit"],
                focus=look["focus"], subs=look["subs"], lang=look["sub_lang"],
                sub_mode=look["sub_mode"], audio_only=audio_only, progress=progress)
        return final_path
    finally:
        if work:
            work.cleanup()


def clip_auto(
    url: str,
    start: str | float,
    end: str | float,
    output_dir: str | Path | None = None,
    quality: Optional[str] = None,
    precise: Optional[bool] = None,
    filename: Optional[str] = None,
    progress: Optional[Callable[[dict], None]] = None,
    mode: Optional[str] = None,
    test: bool = False,
    look: Optional[dict] = None,
) -> Path:
    """Pilih otomatis: livestream -> mode live (unduh segmen saja); selain itu -> mode biasa.

    mode: 'auto' | 'live' | 'vod'.  test=True -> hanya CLIPPER_TEST_SECONDS (bawaan 10) detik pertama dari rentang.
    """
    cfg = config.get()
    mode = cfg.mode if mode is None else mode
    if mode not in ("auto", "live", "vod"):
        raise ValueError("mode harus auto, live, atau vod")
    if mode == "auto":
        status = get_info(url).get("live_status")
        if status == "is_upcoming":
            raise ValueError("Stream belum dimulai. Coba lagi setelah stream berjalan.")
        mode = "live" if status in ("is_live", "post_live") else "vod"
    if mode == "live":
        from live_clip import clip_live
        return clip_live(url, start, end, output_dir, quality, filename, progress, test=test, look=look)
    if is_relative(start) or is_relative(end):
        raise ValueError("Waktu relatif (-mm:ss / now) hanya untuk livestream yang sedang berjalan.")
    if test:
        end = min(parse_time(end), parse_time(start) + cfg.test_seconds)
    return clip(url, start, end, output_dir, quality, precise, filename, progress, look)
