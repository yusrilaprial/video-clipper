"""Frame (rasio/ukuran), mode fit, burn-in subtitle, dan render akhir untuk klip."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

import config
import subtitles as subs_mod


class RenderError(Exception):
    """Kesalahan render yang aman ditampilkan ke pengguna."""


# ------------------------------------------------------------------ frame
_VERT, _HORZ = (1080, 1920), (1920, 1080)
FRAME_PRESETS: dict[str, tuple[int, int]] = {
    # 9:16 (vertikal)
    "tiktok": _VERT, "reels": _VERT, "ig-reels": _VERT, "shorts": _VERT, "yt-shorts": _VERT,
    "story": _VERT, "ig-story": _VERT, "whatsapp-status": _VERT,
    # 4:5 (feed Instagram potret)
    "instagram": (1080, 1350), "ig": (1080, 1350), "ig-feed": (1080, 1350), "portrait": (1080, 1350),
    # 1:1
    "square": (1080, 1080), "ig-square": (1080, 1080),
    # 16:9
    "youtube": _HORZ, "yt": _HORZ, "landscape": _HORZ,
    "twitter": (1280, 720), "x": (1280, 720),
    # lainnya
    "cinema": (1920, 820),
}
FRAME_HELP = ("original | tiktok, reels, shorts, story (9:16) | instagram, ig-feed (4:5) | square (1:1) | "
              "youtube (16:9) | twitter/x (16:9 720p) | cinema (21:9) | rasio 'A:B' (mis. 3:4) | ukuran 'LEBARxTINGGI'")


def _even(n: int) -> int:
    return n + (n % 2)


def resolve_frame(spec: Optional[str]) -> Optional[tuple[int, int]]:
    """Nama/rasio/ukuran -> (lebar, tinggi) genap, atau None untuk 'original'."""
    s = (spec or "original").strip().lower()
    if s in ("", "original", "asli", "none"):
        return None
    if s in FRAME_PRESETS:
        return FRAME_PRESETS[s]
    m = re.fullmatch(r"(\d{2,5})x(\d{2,5})", s)
    if m:
        w, h = _even(int(m[1])), _even(int(m[2]))
        if not (64 <= w <= 4096 and 64 <= h <= 4096):
            raise ValueError("ukuran harus antara 64 dan 4096 piksel")
        return w, h
    m = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", s)
    if m:
        a, b = float(m[1]), float(m[2])
        if a <= 0 or b <= 0 or max(a / b, b / a) > 4:
            raise ValueError("rasio tidak wajar (maksimal 4:1)")
        return (1080, _even(round(1080 * b / a))) if a < b else (_even(round(1080 * a / b)), 1080)
    raise ValueError(f"tidak dikenal. Pilihan: {FRAME_HELP}")


def frame_tag(spec: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9\-x]", "x", (spec or "").strip().lower())


# ------------------------------------------------------------------ ffmpeg util
def _run(cmd: list[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    except FileNotFoundError:
        raise RenderError(f"{cmd[0]} tidak ditemukan. Pasang ffmpeg atau atur CLIPPER_FFMPEG_PATH.") from None
    if r.returncode != 0:
        raise RenderError(f"ffmpeg gagal: {r.stderr.strip()[-500:]}")
    return r


def probe_size(path: Path) -> tuple[int, int]:
    r = _run([config.get().ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
              "stream=width,height", "-of", "csv=p=0:s=x", str(path)])
    m = re.search(r"(\d+)x(\d+)", r.stdout)
    if not m:
        raise RenderError("Ukuran video tidak terbaca")
    return int(m[1]), int(m[2])


def extract_wav(src: Path, dst: Path, t0: Optional[float] = None, t1: Optional[float] = None) -> Path:
    """Audio mono 16 kHz untuk Whisper. t0/t1 (opsional): potong pada timeline segmen (mode live)."""
    cfg = config.get()
    cmd = [cfg.ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if t0 is not None:
        cmd += ["-copyts", "-i", str(src), "-af", f"atrim=start={t0:.3f}:end={t1:.3f},asetpts=PTS-STARTPTS"]
    else:
        cmd += ["-i", str(src)]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]
    _run(cmd)
    return dst


# ------------------------------------------------------------------ subtitle styling
def _sub_metrics(w: int, h: int, scale: float) -> dict:
    size = max(14, round(min(w, h) * 0.052 * scale))
    vertical = h > w * 1.3
    return {
        "size": size,
        "outline": max(2, round(size / 13)),
        "margin_v": round(h * (0.17 if vertical else 0.06)),  # vertikal: hindari UI TikTok/Reels di bawah
        "margin_lr": round(w * 0.06),
        "max_chars": max(14, int(w * 0.88 / (size * 0.55))),
    }


def prepare_cues(cues: list[subs_mod.Cue], wh: tuple[int, int], scale: float) -> list[subs_mod.Cue]:
    """Pecah cue yang terlalu panjang agar muat di frame berukuran wh."""
    return subs_mod.wrap_cues(cues, _sub_metrics(*wh, scale)["max_chars"])


def _ass_time(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    sec, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def to_ass(cues: list[subs_mod.Cue], wh: tuple[int, int], scale: float, font: Optional[str]) -> str:
    """ASS dengan PlayRes = ukuran video, sehingga semua ukuran dalam piksel (SRT memakai kanvas 384x288)."""
    w, h = wh
    m = _sub_metrics(w, h, scale)
    name = re.sub(r"[^\w \-]", "", font) if font else "Arial"
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{name},{m['size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{m['outline']},0,2,{m['margin_lr']},{m['margin_lr']},{m['margin_v']},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for c in cues:
        text = c.text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,{text}")
    return head + "\n".join(lines) + "\n"


# ------------------------------------------------------------------ filter graph
def video_graph(src: str, dst: str, size: Optional[tuple[int, int]],
                fit: str, focus: float, ass_name: Optional[str]) -> str:
    """Bagian filter_complex: [src] -> (frame) -> (burn subtitle .ass) -> [dst]. Label tanpa kurung siku."""
    parts: list[str] = []
    burn = ass_name is not None
    framed = "vgframed" if burn else dst
    cur = src
    if size:
        w, h = size
        if fit == "crop":
            parts.append(f"[{src}]scale={w}:{h}:force_original_aspect_ratio=increase,"
                         f"crop={w}:{h}:(iw-ow)*{focus:.3f}:(ih-oh)/2,setsar=1[{framed}]")
        elif fit == "pad":
            parts.append(f"[{src}]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                         f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[{framed}]")
        else:  # blur: latar buram memenuhi frame + video asli utuh di tengah
            parts.append(f"[{src}]split[vgbgin][vgfgin]")
            parts.append(f"[vgbgin]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                         "boxblur=25:5,setsar=1[vgbg]")
            parts.append(f"[vgfgin]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1[vgfg]")
            parts.append(f"[vgbg][vgfg]overlay=(W-w)/2:(H-h)/2[{framed}]")
        cur = framed
    else:
        if not burn:
            parts.append(f"[{src}]null[{dst}]")
    if burn:
        parts.append(f"[{cur}]ass=filename={ass_name}[{dst}]")
    return ";".join(parts)


def encode_args(audio_only: bool) -> list[str]:
    cfg = config.get()
    if audio_only:
        return ["-c:a", "libmp3lame", "-b:a", cfg.audio_bitrate]
    return ["-c:v", "libx264", "-preset", cfg.video_preset, "-crf", str(cfg.video_crf),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", cfg.audio_bitrate, "-movflags", "+faststart"]


# ------------------------------------------------------------------ render file (mode video biasa)
def postprocess_vod(src: Path, info: dict, t0: float, t1: float, out_dir: Path, *,
                    frame: Optional[str], fit: str, focus: float, subs: str, lang: str,
                    sub_mode: str, audio_only: bool,
                    progress: Optional[Callable[[dict], None]] = None) -> Path:
    """Terapkan frame + subtitle pada klip hasil yt-dlp (src), simpan ke out_dir, return path final."""
    cfg = config.get()
    out_dir.mkdir(parents=True, exist_ok=True)
    size = None if audio_only else resolve_frame(frame)
    mode = "srt" if audio_only else sub_mode
    tag = f" [{frame_tag(frame)}]" if size else ""
    out_path = (out_dir / f"{src.stem}{tag}{src.suffix}").resolve()

    tmp = Path(tempfile.mkdtemp(prefix="vpost_"))
    try:
        cues: list = []
        if subs != "off":
            cues = subs_mod.collect_cues(subs, lang, info, t0, t1,
                                         lambda: extract_wav(src, tmp / "a.wav"), progress)
        if audio_only:
            src_wh = (1080, 1920)
        else:
            src_wh = probe_size(src)
        out_wh = size or src_wh
        cues = prepare_cues(cues, out_wh, cfg.sub_scale) if cues else []
        if cues and mode == "srt":
            out_path.with_suffix(".srt").write_text(subs_mod.to_srt(cues), encoding="utf-8")

        burn = bool(cues) and mode == "burn" and not audio_only
        soft = bool(cues) and mode == "soft" and not audio_only
        if audio_only or (not size and not burn and not soft):
            if src.resolve() != out_path:
                shutil.move(str(src), out_path)
            return out_path

        cmd = [cfg.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
        if burn:
            (tmp / "subs.ass").write_text(to_ass(cues, out_wh, cfg.sub_scale, cfg.sub_font), encoding="utf-8")
        if soft:
            (tmp / "subs.srt").write_text(subs_mod.to_srt(cues), encoding="utf-8")
            cmd += ["-i", str(tmp / "subs.srt")]
        graph = video_graph("0:v:0", "vout", size, fit, focus, "subs.ass" if burn else None)
        cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", "0:a?"]
        if soft:
            cmd += ["-map", "1:0", "-c:s", "mov_text"]
        cmd += encode_args(False) + [str(out_path)]
        _run(cmd, cwd=tmp)
        src.unlink(missing_ok=True)
        return out_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
