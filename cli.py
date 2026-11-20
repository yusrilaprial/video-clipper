#!/usr/bin/env python3
"""CLI: python cli.py URL -s 1:30 -e 2:45 [-q 720] [-o output]
Banyak klip sekaligus: python cli.py URL --clip 0:10-0:40 --clip 5:00-5:30
"""
import argparse
import sys

from clipper import QUALITIES, clip_auto, get_info, format_time


def progress(d):
    if d.get("status") == "downloading":
        pct = d.get("_percent_str", "").strip()
        speed = d.get("_speed_str", "").strip()
        print(f"\r  Mengunduh {pct} {speed}   ", end="", flush=True)
    elif d.get("status") == "finished":
        mb = d.get("downloaded_bytes")
        extra = f" (total diunduh {mb / 1048576:.1f} MB)" if mb else ""
        print(f"\r  Unduhan selesai{extra}, memproses/render...      ")


def main() -> int:
    p = argparse.ArgumentParser(description="Potong video dari link YouTube dan langsung render.")
    p.add_argument("url")
    p.add_argument("-s", "--start", help="Waktu mulai, mis. 1:30 / 00:01:30 / 90 / 1m30s")
    p.add_argument("-e", "--end", help="Waktu akhir")
    p.add_argument("--clip", action="append", metavar="MULAI-AKHIR",
                   help="Klip tambahan format 'mulai-akhir' (boleh diulang), mis. 0:10-0:40")
    p.add_argument("-q", "--quality", default="best", choices=list(QUALITIES))
    p.add_argument("-o", "--output", default="output", help="Folder hasil (default: output)")
    p.add_argument("--fast", action="store_true", help="Potong di keyframe (cepat, kurang presisi; hanya mode vod)")
    p.add_argument("--mode", default="auto", choices=["auto", "live", "vod"],
                   help="auto = deteksi livestream otomatis (default)")
    p.add_argument("--test", action="store_true",
                   help="Uji: hanya 10 detik pertama dari rentang (hemat kuota)")
    a = p.parse_args()

    ranges = []
    if a.start and a.end:
        ranges.append((a.start, a.end))
    for c in a.clip or []:
        if "-" not in c:
            p.error(f"Format --clip salah: {c}")
        s, e = c.rsplit("-", 1)
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
            path = clip_auto(a.url, s, e, a.output, a.quality, precise=not a.fast,
                             progress=progress, mode=a.mode, test=a.test)
            print(f"  Tersimpan: {path}")
    except Exception as ex:  # noqa: BLE001
        print(f"\nGagal: {ex}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
