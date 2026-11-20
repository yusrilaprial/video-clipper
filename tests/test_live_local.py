"""Uji offline mode live: server lokal meniru segmen YouTube (sq=N + X-Head-Seqnum).
Jalankan:  python tests/test_live_local.py     (butuh ffmpeg, tanpa internet)
Video uji: kecerahan layar = detik mod 20; audio: nada 440 Hz hanya pada detik 42-43.
"""
import http.server
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from live_clip import LiveClipError, clip_from_formats  # noqa: E402

DUR = 120


def sh(*cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-800:])
    return r


def split_segments(path):
    data = Path(path).read_bytes()
    pos, head, cur, segs = 0, b"", b"", []
    while pos < len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        typ, box = data[pos + 4:pos + 8], data[pos:pos + size]
        if typ in (b"ftyp", b"moov"):
            head += box
        elif typ == b"moof":
            cur = box
        elif typ == b"mdat":
            segs.append(cur + box)
        pos += size
    segs[0] = head + segs[0]  # sq=0 = init + segmen media pertama (seperti YouTube)
    return segs


def make_sources(tmp, offset=0):
    v, a = tmp / f"v{offset}.mp4", tmp / f"a{offset}.mp4"
    sh("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
       "nullsrc=s=320x180:r=25,format=yuv420p,geq=lum='255*mod(T,20)/20':cb=128:cr=128",
       "-t", str(DUR), "-output_ts_offset", str(offset), "-c:v", "libx264", "-g", "50", "-keyint_min", "50", "-sc_threshold", "0",
       "-pix_fmt", "yuv420p", "-movflags", "empty_moov+default_base_moof+frag_keyframe", str(v))
    sh("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
       f"aevalsrc='if(between(t,42,43),0.5*sin(2*PI*440*t),0)':s=44100:d={DUR}",
       "-output_ts_offset", str(offset), "-c:a", "aac", "-b:a", "96k",
       "-frag_duration", "2000000", "-movflags", "empty_moov+default_base_moof", str(a))
    return split_segments(v), split_segments(a)


def serve(tracks):
    stats = {"bytes": 0, "requests": 0}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _track(self):
            return tracks[urlparse(self.path).path.strip("/")]

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("X-Head-Seqnum", str(len(self._track()) - 1))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            segs = self._track()
            sq = int(parse_qs(urlparse(self.path).query).get("sq", ["0"])[0])
            if sq >= len(segs):
                self.send_response(404); self.end_headers(); return
            body = segs[sq]
            stats["bytes"] += len(body); stats["requests"] += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, stats


def want(y):
    """nilai Y (limited range dari geq) -> nilai gray hasil konversi ffmpeg"""
    return max(0, min(255, (y - 16) * 255 / 219))


def luma_at(path, t):
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                        "-vf", "scale=16:16,format=gray", "-f", "rawvideo", "-"], capture_output=True)
    return sum(r.stdout) / len(r.stdout)


def duration(path):
    return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "csv=p=0", str(path)).stdout.strip())


def tone_start(path):
    r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "silencedetect=noise=-30dB:d=0.3", "-f", "null", "-"],
                       capture_output=True, text=True)
    ends = [float(l.split("silence_end:")[1].split()[0]) for l in r.stderr.splitlines() if "silence_end" in l]
    return ends[0] if ends else None


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name, detail)
    return cond


def main():
    ok = True
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        vsegs, asegs = make_sources(tmp)
        srv, stats = serve({"video": vsegs, "audio": asegs})
        port = srv.server_address[1]
        total = sum(map(len, vsegs)) + sum(map(len, asegs))
        vf = {"url": f"http://127.0.0.1:{port}/video?x=1", "target_duration": 2}
        af = {"url": f"http://127.0.0.1:{port}/audio?x=1", "target_duration": 2}
        print(f"Total stream simulasi: {total/1024:.0f} KB, {len(vsegs)} segmen/track")

        # 1) klip di tengah: 37.5 - 47.5 (sq awal > 0 -> pakai init sq=0)
        stats.update(bytes=0, requests=0)
        out = clip_from_formats("uji", vf, af, "0:37.5", "0:47.5", tmp / "o1")
        ok &= check("durasi 10 dtk", abs(duration(out) - 10) < 0.3, f"{duration(out):.2f}s")
        l0, w0 = luma_at(out, 0.1), want(255 * 17.6 / 20)    # T=37.6
        ok &= check("frame awal = detik 37.5", abs(l0 - w0) < 8, f"luma={l0:.0f} (target ~{w0:.0f})")
        l45, w45 = luma_at(out, 4.6), want(255 * 2.1 / 20)   # T=42.1
        ok &= check("frame di 4.6s = detik 42.1", abs(l45 - w45) < 8, f"luma={l45:.0f} (target ~{w45:.0f})")
        ts = tone_start(out)
        ok &= check("nada audio mulai ~4.5s (sinkron)", ts is not None and abs(ts - 4.5) < 0.25, f"{ts}")
        frac = stats["bytes"] / total
        ok &= check("hanya sebagian kecil diunduh", frac < 0.3, f"{stats['bytes']/1024:.0f} KB = {frac*100:.0f}% dari total")

        # 2) klip dari awal (a == 0 tanpa init terpisah)
        out2 = clip_from_formats("uji", vf, af, 0, 6, tmp / "o2")
        ok &= check("klip awal durasi 6 dtk", abs(duration(out2) - 6) < 0.3, f"{duration(out2):.2f}s")
        ok &= check("frame awal ~ detik 0", luma_at(out2, 0.1) < 12, f"luma={luma_at(out2, 0.1):.0f}")

        # 2b) klip jauh di tengah (drift panjang segmen audio tidak boleh mengganggu)
        out2b = clip_from_formats("uji", vf, af, "1:20", "1:30", tmp / "o2b")
        w = want(255 * 0.1 / 20 * 0 + 255 * ((80.1) % 20) / 20)
        ok &= check("klip 1:20-1:30 frame awal benar", abs(luma_at(out2b, 0.1) - w) < 10, f"luma={luma_at(out2b, 0.1):.0f} (target ~{w:.0f})")

        # 3) audio saja
        out3 = clip_from_formats("uji", None, af, 40, 46, tmp / "o3")
        ok &= check("audio mp3 durasi 6 dtk", out3.suffix == ".mp3" and abs(duration(out3) - 6) < 0.3, f"{duration(out3):.2f}s")
        ts3 = tone_start(out3)
        ok &= check("nada mulai ~2.0s", ts3 is not None and abs(ts3 - 2.0) < 0.25, f"{ts3}")

        # 3b) timeline stream bergeser +100 detik (tfdt tidak mulai dari 0)
        vs2, as2 = make_sources(tmp, offset=100)
        srv2, _ = serve({"video": vs2, "audio": as2})
        p2 = srv2.server_address[1]
        vf2 = {"url": f"http://127.0.0.1:{p2}/video?x=1", "target_duration": 2}
        af2 = {"url": f"http://127.0.0.1:{p2}/audio?x=1", "target_duration": 2}
        out5 = clip_from_formats("uji", vf2, af2, "0:37.5", "0:47.5", tmp / "o5")
        l5, w5 = luma_at(out5, 0.1), want(255 * 17.6 / 20)
        ok &= check("timeline bergeser: frame awal benar", abs(l5 - w5) < 8, f"luma={l5:.0f} (target ~{w5:.0f})")
        ts5 = tone_start(out5)
        ok &= check("timeline bergeser: nada ~4.5s", ts5 is not None and abs(ts5 - 4.5) < 0.25, f"{ts5}")
        srv2.shutdown()

        # 4) melewati live edge
        try:
            clip_from_formats("uji", vf, af, 100, 500, tmp / "o4")
            ok &= check("error melewati live edge", False)
        except LiveClipError as e:
            ok &= check("error melewati live edge", "live edge" in str(e), str(e))
        srv.shutdown()
    print("\nSEMUA LULUS" if ok else "\nADA YANG GAGAL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
