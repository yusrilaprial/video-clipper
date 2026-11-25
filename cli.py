#!/usr/bin/env python3
"""CLI: python cli.py URL -s 1:30 -e 2:45 [-q 720] [-o output]
Livestream (waktu relatif ke sekarang): python cli.py URL -s -1:30 -e 0:00
Banyak klip sekaligus: python cli.py URL --clip 0:10-0:40 --clip 5:00-5:30
Frame sosmed: python cli.py URL -s 1:30 -e 2:00 --frame tiktok [--fit blur|crop|pad]
Subtitle (bahasa terdeteksi otomatis): python cli.py URL -s 1:30 -e 2:00 --subs auto [--sub-lang id] [--sub-mode burn|soft|srt]
Lihat konfigurasi efektif: python cli.py --show-config   |   daftar frame: python cli.py --list-frames
Konfigurasi bawaan dibaca dari environment variable / file .env (CLIPPER_*, lihat .env.example).
"""
import argparse
import json
import sys

import config


def progress(d):
    if d.get("status") == "message":
        icon = "!" if d.get("level") == "warning" else "i"
        print(f"\r  [{icon}] {d.get('message')}                    ")
    elif d.get("status") == "downloading":
        pct = d.get("_percent_str", "").strip()
        speed = d.get("_speed_str", "").strip()
        print(f"\r  Mengunduh {pct} {speed}   ", end="", flush=True)
    elif d.get("status") == "finished":
        mb = d.get("downloaded_bytes")
        extra = f" (total diunduh {mb / 1048576:.1f} MB)" if mb else ""
        print(f"\r  Unduhan selesai{extra}, memproses/render...      ")


def _fix_negative_args(argv):
    """argparse menganggap '-1:30' sebagai opsi; ubah '-s -1:30' menjadi '-s=-1:30'."""
    out, i = [], 0
    while i < len(argv):
        t = argv[i]
        if t in ("-s", "--start", "-e", "--end", "--clip") and i + 1 < len(argv) and argv[i + 1].startswith("-"):
            out.append(f"{t}={argv[i + 1]}")
            i += 2
        else:
            out.append(t)
            i += 1
    return out


def main() -> int:
    try:
        cfg = config.get()
    except config.ConfigError as ex:
        print(f"Konfigurasi salah: {ex}", file=sys.stderr)
        return 2

    from clipper import QUALITIES, clip_auto, format_time, get_info

    p = argparse.ArgumentParser(description="Potong video dari link YouTube dan langsung render.")
    p.add_argument("url", nargs="?")
    p.add_argument("-s", "--start", help="Waktu mulai, mis. 1:30 / 00:01:30 / 90 / 1m30s. "
                   "Livestream: '-1:30' = 1 mnt 30 dtk yang lalu")
    p.add_argument("-e", "--end", help="Waktu akhir. Livestream: '0:00' atau 'now' = sekarang")
    p.add_argument("--clip", action="append", metavar="MULAI-AKHIR",
                   help="Klip tambahan 'mulai-akhir' (boleh diulang), mis. 0:10-0:40; "
                   "untuk waktu relatif pakai '..', mis. -1:30..0:00")
    p.add_argument("-q", "--quality", default=cfg.quality, choices=list(QUALITIES),
                   help=f"Kualitas (bawaan: {cfg.quality})")
    p.add_argument("-o", "--output", default=cfg.output_dir, help=f"Folder hasil (bawaan: {cfg.output_dir})")
    fast = p.add_mutually_exclusive_group()
    fast.add_argument("--fast", dest="precise", action="store_false",
                      help="Potong di keyframe (cepat, kurang presisi; hanya mode vod)")
    fast.add_argument("--precise", dest="precise", action="store_true", help="Potong presisi (render ulang titik potong)")
    p.set_defaults(precise=cfg.precise)
    p.add_argument("--mode", default=cfg.mode, choices=["auto", "live", "vod"],
                   help=f"auto = deteksi livestream otomatis (bawaan: {cfg.mode})")
    p.add_argument("--test", action="store_true",
                   help=f"Uji: hanya {cfg.test_seconds:g} detik pertama dari rentang (hemat kuota)")
    import render
    p.add_argument("--frame", default=None, metavar="NAMA",
                   help=f"Frame/rasio hasil (bawaan: {cfg.frame}). {render.FRAME_HELP}")
    p.add_argument("--fit", default=None, choices=list(config.VALID_FITS),
                   help=f"Cara mengisi frame: blur = latar buram, crop = potong tengah, pad = bingkai hitam (bawaan: {cfg.fit})")
    p.add_argument("--focus", type=float, default=None, metavar="0-1",
                   help=f"Posisi horizontal potongan untuk --fit crop: 0 kiri, 0.5 tengah, 1 kanan (bawaan: {cfg.focus:g})")
    p.add_argument("--subs", default=None, choices=list(config.VALID_SUBS),
                   help="Subtitle: off | youtube (caption YouTube) | whisper (transkripsi AI) | "
                        f"auto (caption YouTube, lalu Whisper). Bawaan: {cfg.subs}")
    p.add_argument("--sub-lang", default=None, metavar="KODE",
                   help=f"Bahasa subtitle: auto (deteksi otomatis) atau kode seperti id, en, ja (bawaan: {cfg.sub_lang})")
    p.add_argument("--sub-mode", default=None, choices=list(config.VALID_SUB_MODES),
                   help=f"burn = tertanam di video, soft = track yang bisa dimatikan, srt = file .srt terpisah (bawaan: {cfg.sub_mode})")
    p.add_argument("--list-frames", action="store_true", help="Tampilkan daftar preset frame lalu keluar")
    p.add_argument("--show-config", action="store_true", help="Tampilkan konfigurasi efektif lalu keluar")
    a = p.parse_args(_fix_negative_args(sys.argv[1:]))

    if a.show_config:
        print(json.dumps(config.describe(cfg), indent=2, ensure_ascii=False))
        return 0
    if a.list_frames:
        for name, (w, h) in sorted(render.FRAME_PRESETS.items(), key=lambda kv: (kv[1], kv[0])):
            print(f"{name:<16} {w}x{h}")
        print("original         ukuran asli video\n(juga: rasio 'A:B' mis. 3:4, atau ukuran 'LEBARxTINGGI' mis. 720x1280)")
        return 0
    if not a.url:
        p.error("url wajib diisi")
    look = {"frame": a.frame, "fit": a.fit, "focus": a.focus, "subs": a.subs,
            "sub_lang": a.sub_lang, "sub_mode": a.sub_mode}
    from clipper import resolve_look
    try:
        resolve_look(look)  # validasi cepat sebelum menghubungi YouTube
    except ValueError as ex:
        p.error(str(ex))

    ranges = []
    if a.start and a.end:
        ranges.append((a.start, a.end))
    for c in a.clip or []:
        if ".." in c:
            s, e = c.split("..", 1)
        elif "-" in c and not c.startswith("-"):
            s, e = c.rsplit("-", 1)
        else:
            p.error(f"Format --clip salah: {c} (pakai mulai-akhir, atau mulai..akhir untuk waktu relatif)")
        ranges.append((s, e))
    if not ranges:
        p.error("Isi -s dan -e, atau gunakan --clip mulai-akhir")

    try:
        info = get_info(a.url)
        dur = f" ({format_time(info['duration'])})" if info.get("duration") else ""
        status = info.get("live_status")
        badge = " [LIVE]" if status == "is_live" else (" [BARU SELESAI LIVE]" if status == "post_live" else "")
        print(f"Video: {info['title']}{dur}{badge}")
        if a.mode == "auto" and status in ("is_live", "post_live"):
            print("  Mode live: hanya segmen yang dibutuhkan yang diunduh.")
        for i, (s, e) in enumerate(ranges, 1):
            print(f"[{i}/{len(ranges)}] Klip {s} -> {e}")
            path = clip_auto(a.url, s, e, a.output, a.quality, precise=a.precise,
                             progress=progress, mode=a.mode, test=a.test, look=look)
            print(f"  Tersimpan: {path}")
    except Exception as ex:  # noqa: BLE001
        import cookies as ck
        import yterrors
        d = yterrors.explain(str(ex), ck.active_source() is not None)
        print(f"\nGagal: {d['message']}", file=sys.stderr)
        if d["hint"]:
            print(f"Saran: {d['hint']}", file=sys.stderr)
        if d["code"] in ("bot_check", "age", "members", "private"):
            print("       Di CLI: isi CLIPPER_COOKIES_FILE (cookies.txt) atau CLIPPER_COOKIES_FROM_BROWSER di .env.", file=sys.stderr)
        if d["code"] != "other":
            print(f"Detail: {d['detail'][:300]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
