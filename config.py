"""Konfigurasi aplikasi lewat environment variable atau file .env (semua berawalan CLIPPER_).

Urutan prioritas: environment variable asli > file .env > nilai bawaan.
File .env dicari di: $CLIPPER_ENV_FILE, lalu folder kerja saat ini, lalu folder proyek ini.
Gunakan config.get() untuk membaca konfigurasi (dibaca sekali, lalu di-cache).
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

PREFIX = "CLIPPER_"
VALID_QUALITIES = ("best", "1080", "720", "480", "360", "audio")
VALID_MODES = ("auto", "live", "vod")
VALID_FITS = ("blur", "crop", "pad")
VALID_SUBS = ("off", "auto", "youtube", "whisper")
VALID_SUB_MODES = ("burn", "soft", "srt")
X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")


class ConfigError(ValueError):
    """Nilai konfigurasi tidak valid."""


# ------------------------------------------------------------------ .env
def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"{path}:{n}: baris tidak valid (harus KEY=VALUE): {line!r}")
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        elif " #" in val:  # komentar di akhir baris (hanya untuk nilai tanpa kutip)
            val = val.split(" #", 1)[0].rstrip()
        values[key] = val
    return values


def _dotenv_values() -> dict[str, str]:
    explicit = os.environ.get(PREFIX + "ENV_FILE")
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise ConfigError(f"{PREFIX}ENV_FILE menunjuk ke file yang tidak ada: {p}")
        return _parse_dotenv(p)
    merged: dict[str, str] = {}
    seen: set[Path] = set()
    for base in (Path(__file__).resolve().parent, Path.cwd()):  # folder kerja menimpa folder proyek
        p = (base / ".env").resolve()
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        merged.update(_parse_dotenv(p))
    return merged


class _Env:
    def __init__(self) -> None:
        self.file = _dotenv_values()

    def raw(self, name: str) -> Optional[str]:
        key = PREFIX + name
        v = os.environ.get(key)
        if v is None:
            v = self.file.get(key)
        v = None if v is None else v.strip()
        return v or None

    def _bad(self, name: str, v: str, why: str) -> ConfigError:
        return ConfigError(f"{PREFIX}{name}={v!r} tidak valid: {why}")

    def str(self, name: str, default: Optional[str] = None) -> Optional[str]:
        v = self.raw(name)
        return default if v is None else v

    def int(self, name: str, default: int, lo: int, hi: int) -> int:
        v = self.raw(name)
        if v is None:
            return default
        try:
            n = int(v)
        except ValueError:
            raise self._bad(name, v, "harus bilangan bulat") from None
        if not lo <= n <= hi:
            raise self._bad(name, v, f"harus antara {lo} dan {hi}")
        return n

    def float(self, name: str, default: float, lo: float, hi: float) -> float:
        v = self.raw(name)
        if v is None:
            return default
        try:
            n = float(v)
        except ValueError:
            raise self._bad(name, v, "harus angka") from None
        if not lo <= n <= hi:
            raise self._bad(name, v, f"harus antara {lo} dan {hi}")
        return n

    def bool(self, name: str, default: bool) -> bool:
        v = self.raw(name)
        if v is None:
            return default
        s = v.lower()
        if s in ("1", "true", "yes", "y", "on", "ya"):
            return True
        if s in ("0", "false", "no", "n", "off", "tidak"):
            return False
        raise self._bad(name, v, "gunakan true/false")

    def choice(self, name: str, default: str, choices: tuple[str, ...]) -> str:
        v = self.raw(name)
        if v is None:
            return default
        v = v.lower()
        if v not in choices:
            raise self._bad(name, v, f"pilih salah satu: {', '.join(choices)}")
        return v


# ------------------------------------------------------------------ model
@dataclass(frozen=True)
class Config:
    # umum
    output_dir: str
    quality: str
    mode: str
    precise: bool
    test_seconds: float
    title_max: int
    # frame / ukuran video
    frame: str
    fit: str
    focus: float
    # subtitle
    subs: str
    sub_lang: str
    sub_mode: str
    sub_scale: float
    sub_font: Optional[str]
    whisper_model: str
    whisper_device: str
    whisper_compute: str
    whisper_dir: Optional[str]
    # ffmpeg
    ffmpeg: str
    ffprobe: str
    ffmpeg_location: Optional[str]
    # jaringan / akun
    proxy: Optional[str]
    cookies_file: Optional[str]
    cookies_from_browser: Optional[str]
    # mode live
    live_workers: int
    live_margin: int
    live_retries: int
    http_timeout: int
    # hasil render
    video_preset: str
    video_crf: int
    audio_bitrate: str
    # web UI
    host: str
    port: int
    # deployment (web)
    auth_user: Optional[str]
    auth_password: Optional[str]
    max_jobs: int
    max_clip_seconds: int
    retention_hours: float
    allowed_domains: tuple


def _resolve_ffmpeg(raw: Optional[str], probe_raw: Optional[str]) -> tuple[str, str, Optional[str]]:
    exe = ".exe" if os.name == "nt" else ""
    if not raw:
        return "ffmpeg", probe_raw or "ffprobe", None
    p = Path(raw).expanduser()
    if p.is_dir():
        return str(p / f"ffmpeg{exe}"), probe_raw or str(p / f"ffprobe{exe}"), str(p)
    sibling = p.with_name(p.name.replace("ffmpeg", "ffprobe"))
    probe = probe_raw or (str(sibling) if sibling.exists() and sibling != p else "ffprobe")
    return str(p), probe, str(p)


def load() -> Config:
    e = _Env()
    ffmpeg, ffprobe, ffloc = _resolve_ffmpeg(e.str("FFMPEG_PATH"), e.str("FFPROBE_PATH"))

    cookies_file = e.str("COOKIES_FILE")
    if cookies_file:
        cookies_file = str(Path(cookies_file).expanduser())
        if not Path(cookies_file).is_file():
            raise ConfigError(f"{PREFIX}COOKIES_FILE tidak ditemukan: {cookies_file}")

    bitrate = e.str("AUDIO_BITRATE", "192k")
    if not re.fullmatch(r"\d{2,3}k", bitrate or ""):
        raise ConfigError(f"{PREFIX}AUDIO_BITRATE={bitrate!r} tidak valid: contoh 128k, 192k, 320k")

    return Config(
        output_dir=str(Path(e.str("OUTPUT_DIR", "output")).expanduser()),
        quality=e.choice("QUALITY", "best", VALID_QUALITIES),
        mode=e.choice("MODE", "auto", VALID_MODES),
        precise=e.bool("PRECISE", True),
        test_seconds=e.float("TEST_SECONDS", 10.0, 1, 300),
        title_max=e.int("TITLE_MAX", 80, 20, 200),
        frame=(e.str("FRAME", "original") or "original").strip().lower(),
        fit=e.choice("FIT", "blur", VALID_FITS),
        focus=e.float("FOCUS", 0.5, 0.0, 1.0),
        subs=e.choice("SUBS", "off", VALID_SUBS),
        sub_lang=(e.str("SUB_LANG", "auto") or "auto").strip().lower(),
        sub_mode=e.choice("SUB_MODE", "burn", VALID_SUB_MODES),
        sub_scale=e.float("SUB_SCALE", 1.0, 0.5, 2.0),
        sub_font=e.str("SUB_FONT"),
        whisper_model=e.str("WHISPER_MODEL", "base"),
        whisper_device=e.choice("WHISPER_DEVICE", "auto", ("auto", "cpu", "cuda")),
        whisper_compute=e.str("WHISPER_COMPUTE", "auto"),
        whisper_dir=e.str("WHISPER_DIR"),
        ffmpeg=ffmpeg, ffprobe=ffprobe, ffmpeg_location=ffloc,
        proxy=e.str("PROXY"),
        cookies_file=cookies_file,
        cookies_from_browser=e.str("COOKIES_FROM_BROWSER"),
        live_workers=e.int("LIVE_WORKERS", 4, 1, 16),
        live_margin=e.int("LIVE_MARGIN", 1, 0, 5),
        live_retries=e.int("LIVE_RETRIES", 4, 1, 10),
        http_timeout=e.int("HTTP_TIMEOUT", 30, 5, 300),
        video_preset=e.choice("VIDEO_PRESET", "veryfast", X264_PRESETS),
        video_crf=e.int("VIDEO_CRF", 20, 0, 51),
        audio_bitrate=bitrate,
        host=e.str("HOST", "127.0.0.1"),
        port=e.int("PORT", 5000, 1, 65535),
        auth_user=e.str("AUTH_USER"),
        auth_password=e.str("AUTH_PASSWORD"),
        max_jobs=e.int("MAX_JOBS", 2, 1, 16),
        max_clip_seconds=e.int("MAX_CLIP_SECONDS", 0, 0, 86400),
        retention_hours=e.float("RETENTION_HOURS", 0.0, 0.0, 24 * 365),
        allowed_domains=tuple(d.strip().lower().lstrip(".") for d in (e.str("ALLOWED_DOMAINS", "") or "").split(",") if d.strip()),
    )


def _validate(c: Config) -> Config:
    from render import resolve_frame  # impor lokal: render membaca config
    try:
        resolve_frame(c.frame)
    except ValueError as ex:
        raise ConfigError(f"{PREFIX}FRAME={c.frame!r} tidak valid: {ex}") from None
    if bool(c.auth_user) != bool(c.auth_password):
        raise ConfigError(f"{PREFIX}AUTH_USER dan {PREFIX}AUTH_PASSWORD harus diisi bersamaan")
    return c


@lru_cache(maxsize=1)
def get() -> Config:
    return _validate(load())


def reload() -> Config:
    get.cache_clear()
    return get()


def describe(c: Optional[Config] = None) -> dict:
    """Konfigurasi efektif untuk ditampilkan (password proxy disamarkan)."""
    d = asdict(c or get())
    if d.get("auth_password"):
        d["auth_password"] = "***"
    if d.get("proxy"):
        u = urlsplit(d["proxy"])
        if u.password:
            netloc = f"{u.username}:***@{u.hostname}" + (f":{u.port}" if u.port else "")
            d["proxy"] = urlunsplit(u._replace(netloc=netloc))
    d["_ffmpeg_ditemukan"] = bool(shutil.which(d["ffmpeg"]))
    d["_ffprobe_ditemukan"] = bool(shutil.which(d["ffprobe"]))
    return d
