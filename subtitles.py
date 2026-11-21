"""Subtitle untuk klip: caption YouTube (manual/otomatis) atau transkripsi Whisper (deteksi bahasa otomatis)."""
from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

import config


class SubtitleError(Exception):
    """Kesalahan subtitle yang aman ditampilkan ke pengguna."""


@dataclass
class Cue:
    start: float
    end: float
    text: str


# ------------------------------------------------------------------ format
_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TAG = re.compile(r"<[^>]*>")


def _ts(s: str) -> float:
    m = _TS.fullmatch(s.strip())
    if not m:
        raise ValueError(s)
    h, mi, sec, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(sec) + int(ms.ljust(3, "0")) / 1000


def parse_vtt(text: str) -> list[Cue]:
    """Parse WebVTT. Menangani caption otomatis YouTube (baris berulang + tag waktu per kata)."""
    raw: list[Cue] = []
    blocks = re.split(r"\n{2,}", text.replace("\r\n", "\n").replace("\r", "\n"))  # baris berisi spasi BUKAN pemisah
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            if "-->" in ln:
                try:
                    a, b = ln.split("-->", 1)
                    start, end = _ts(a), _ts(b.strip().split()[0])
                except (ValueError, IndexError):
                    break
                body = [re.sub(r"\s+", " ", _TAG.sub("", t)).strip() for t in lines[i + 1:]]
                raw.append(Cue(start, end, "\n".join(t for t in body if t)))
                break
    # buang duplikasi "rolling" caption otomatis: hanya baris yang belum tampil di cue sebelumnya
    out: list[Cue] = []
    prev: list[str] = []
    for c in raw:
        lines = [t for t in c.text.split("\n") if t]
        new = [t for t in lines if t not in prev]
        prev = lines or prev
        if not new or c.end - c.start < 0.05:
            continue
        out.append(Cue(c.start, c.end, " ".join(new)))
    for a, b in zip(out, out[1:]):  # cue tidak boleh bertumpuk
        if b.start < a.end:
            a.end = max(a.start + 0.05, b.start)
    return out


def clip_cues(cues: list[Cue], t0: float, t1: float) -> list[Cue]:
    """Ambil cue pada [t0, t1], geser agar 0 = awal klip."""
    out = []
    for c in cues:
        if c.end <= t0 or c.start >= t1:
            continue
        s, e = max(c.start, t0) - t0, min(c.end, t1) - t0
        if e - s >= 0.05 and c.text.strip():
            out.append(Cue(s, e, c.text.strip()))
    return out


def _srt_time(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    return "".join(f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n\n"
                   for i, c in enumerate(cues, 1))


def wrap_cues(cues: list[Cue], max_chars: int) -> list[Cue]:
    """Pecah cue panjang (> 2 baris) menjadi beberapa cue, waktu dibagi sebanding jumlah huruf."""
    limit = max(max_chars * 2, 20)
    out: list[Cue] = []
    for c in cues:
        if len(c.text) <= limit:
            out.append(c)
            continue
        chunks, cur = [], ""
        for w in c.text.split():
            if cur and len(cur) + 1 + len(w) > limit:
                chunks.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            chunks.append(cur)
        total, t = sum(len(x) for x in chunks), c.start
        for x in chunks:
            d = (c.end - c.start) * len(x) / total
            out.append(Cue(t, t + d, x))
            t += d
    return out


# ------------------------------------------------------------------ caption YouTube
def _fetch_text(url: str) -> str:
    cfg = config.get()
    handlers = [urllib.request.ProxyHandler({"http": cfg.proxy, "https": cfg.proxy})] if cfg.proxy else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with opener.open(req, timeout=cfg.http_timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        raise SubtitleError(f"Gagal mengunduh caption YouTube: {e}") from e


def _match(d: dict, lang: str) -> Optional[str]:
    keys = [k for k in d if k != "live_chat"]
    for k in keys:
        if k.lower() == lang:
            return k
    for k in keys:
        if k.lower().startswith(lang + "-") and not k.lower().endswith("-orig"):
            return k
    for k in keys:
        if k.lower() == f"{lang}-orig":
            return k
    return None


def select_captions(info: dict, lang: str) -> Optional[tuple[str, str, str]]:
    """Return (kode_bahasa, jenis 'manual'|'otomatis', url vtt) atau None."""
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}
    cands: list[tuple[str, str, dict]] = []
    if lang != "auto":
        for d, kind in ((manual, "manual"), (auto, "otomatis")):
            k = _match(d, lang)
            if k:
                cands.append((k, kind, d))
    else:
        orig = (info.get("language") or "").lower()
        if orig:
            for d, kind in ((manual, "manual"), (auto, "otomatis")):
                k = _match(d, orig)
                if k:
                    cands.append((k, kind, d))
        for k in manual:
            cands.append((k, "manual", manual))
        for k in auto:
            if k.lower().endswith("-orig"):
                cands.append((k, "otomatis", auto))
    for k, kind, d in cands:
        for f in d.get(k) or []:
            if f.get("ext") == "vtt" and f.get("url"):
                return k, kind, f["url"]
    return None


# ------------------------------------------------------------------ Whisper
@lru_cache(maxsize=2)
def _model(name: str, device: str, compute: str, root: Optional[str]):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SubtitleError("faster-whisper belum terpasang. Jalankan: pip install faster-whisper "
                            "(atau pakai --subs youtube bila video punya caption).") from None
    return WhisperModel(name, device=device, compute_type=compute, download_root=root)


def transcribe(wav_path: Path, lang: str) -> tuple[list[Cue], str]:
    """Transkripsi dengan faster-whisper. lang='auto' -> bahasa dideteksi otomatis."""
    cfg = config.get()
    model = _model(cfg.whisper_model, cfg.whisper_device, cfg.whisper_compute, cfg.whisper_dir)
    try:
        segs, info = model.transcribe(str(wav_path), language=None if lang == "auto" else lang,
                                      vad_filter=True, beam_size=5)
        cues = [Cue(float(s.start), float(s.end), s.text.strip()) for s in segs if s.text.strip()]
    except Exception as e:  # noqa: BLE001
        raise SubtitleError(f"Transkripsi Whisper gagal: {e}") from e
    return cues, getattr(info, "language", lang)


# ------------------------------------------------------------------ orkestrasi
Notify = Optional[Callable[[dict], None]]


def _say(cb: Notify, level: str, msg: str) -> None:
    if cb:
        cb({"status": "message", "level": level, "message": msg})


def collect_cues(mode: str, lang: str, info: Optional[dict], t0: float, t1: float,
                 wav_provider: Callable[[], Path], notify: Notify = None) -> list[Cue]:
    """Kumpulkan cue untuk klip [t0, t1] (cue sudah digeser: 0 = awal klip).

    mode: 'youtube' | 'whisper' | 'auto' (coba caption YouTube, lalu Whisper).
    'auto' tidak pernah menggagalkan klip: bila semua gagal, klip dibuat tanpa subtitle + peringatan.
    """
    strict = mode != "auto"
    errors: list[str] = []

    if mode in ("youtube", "auto"):
        try:
            sel = select_captions(info or {}, lang)
            if sel is None:
                raise SubtitleError("video ini tidak punya caption YouTube"
                                    + ("" if lang == "auto" else f" untuk bahasa '{lang}'"))
            code, kind, url = sel
            cues = clip_cues(parse_vtt(_fetch_text(url)), t0, t1)
            if cues:
                _say(notify, "info", f"Subtitle: caption YouTube {kind}, bahasa {code}")
                return cues
            raise SubtitleError("caption YouTube kosong pada rentang waktu ini")
        except SubtitleError as e:
            errors.append(str(e))
            if mode == "youtube":
                raise

    if mode in ("whisper", "auto"):
        try:
            cues, detected = transcribe(wav_provider(), lang)
            if not cues:
                _say(notify, "warning", "Subtitle: tidak ada ucapan terdeteksi pada klip ini")
                return []
            _say(notify, "info", f"Subtitle: Whisper, bahasa terdeteksi '{detected}'"
                 + (" (otomatis)" if lang == "auto" else ""))
            return cues
        except SubtitleError as e:
            errors.append(str(e))
            if strict:
                raise

    _say(notify, "warning", "Subtitle dilewati: " + "; ".join(errors))
    return []
