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

        # 3c) waktu relatif ke live edge: "-0:10" .. "0:00" (edge simulasi = 59*2 = 118 dtk)
        out6 = clip_from_formats("uji", vf, af, "-0:10", "0:00", tmp / "o6")
        ok &= check("relatif -0:10..0:00 durasi 10 dtk", abs(duration(out6) - 10) < 0.3, f"{duration(out6):.2f}s")
        l6, w6 = luma_at(out6, 0.1), want(255 * ((108.1) % 20) / 20)
        ok &= check("relatif: frame awal = detik 108", abs(l6 - w6) < 8, f"luma={l6:.0f} (target ~{w6:.0f})")
        out7 = clip_from_formats("uji", vf, af, "-0:30", "-0:20", tmp / "o7")
        l7, w7 = luma_at(out7, 0.1), want(255 * ((88.1) % 20) / 20)
        ok &= check("relatif -0:30..-0:20 frame awal = detik 88", abs(l7 - w7) < 8, f"luma={l7:.0f} (target ~{w7:.0f})")
        for label, a, b, exc in [("relatif melewati awal stream", "-5:00", "0:00", LiveClipError),
                                 ("campuran relatif+absolut ditolak", "-0:10", "1:00", ValueError)]:
            try:
                clip_from_formats("uji", vf, af, a, b, tmp / "ox")
                ok &= check(label, False)
            except exc as e:
                ok &= check(label, True, str(e))

        # 3d) konfigurasi via env memengaruhi hasil
        import os
        import config
        keep = {k: v for k, v in os.environ.items() if not k.startswith("CLIPPER_")}
        def run_with(**kw):
            os.environ.clear(); os.environ.update(keep)
            os.environ.update({f"CLIPPER_{k}": str(v) for k, v in kw.items()})
            config.reload()
            return clip_from_formats("uji", vf, af, "0:10", "0:20")  # tanpa output_dir -> dari env
        outdir = tmp / "dari_env"
        hi = run_with(OUTPUT_DIR=outdir, VIDEO_CRF=5, AUDIO_BITRATE="320k")
        ok &= check("CLIPPER_OUTPUT_DIR dipakai", hi.parent == outdir and hi.exists(), str(hi.parent.name))
        lo_dir = tmp / "dari_env2"
        lo = run_with(OUTPUT_DIR=lo_dir, VIDEO_CRF=45, VIDEO_PRESET="ultrafast", AUDIO_BITRATE="64k")
        ok &= check("CLIPPER_VIDEO_CRF memengaruhi ukuran", lo.stat().st_size < hi.stat().st_size,
                    f"crf5={hi.stat().st_size} B, crf45={lo.stat().st_size} B")
        try:
            run_with(OUTPUT_DIR=tmp / "x3", FFMPEG_PATH="/tidak/ada/ffmpeg")
            ok &= check("ffmpeg salah -> pesan ramah", False)
        except LiveClipError as e:
            ok &= check("ffmpeg salah -> pesan ramah", "CLIPPER_FFMPEG_PATH" in str(e), str(e)[:70])
        os.environ.clear(); os.environ.update(keep); config.reload()

        # 3e) frame + subtitle (Whisper palsu: deteksi bahasa otomatis) pada mode live
        import types
        from types import SimpleNamespace
        import subtitles
        calls = {}

        class FakeModel:
            def __init__(self, *a, **k): pass
            def transcribe(self, path, language=None, vad_filter=True, beam_size=5):
                calls["language"] = language
                calls["wav_dur"] = duration(Path(path))
                segs = [SimpleNamespace(start=1.0, end=3.0, text=" halo semua selamat datang "),
                        SimpleNamespace(start=5.0, end=6.0, text="")]
                return iter(segs), SimpleNamespace(language="id", language_probability=0.97)

        fake = types.ModuleType("faster_whisper"); fake.WhisperModel = FakeModel
        sys.modules["faster_whisper"] = fake
        subtitles._model.cache_clear()
        msgs = []
        base = {"frame": "tiktok", "fit": "blur"}
        plain = clip_from_formats("uji", vf, af, "0:20", "0:30", tmp / "o8a", look={**base, "subs": "off"})
        burned = clip_from_formats("uji", vf, af, "0:20", "0:30", tmp / "o8b", progress=msgs.append,
                                   look={**base, "subs": "whisper", "sub_mode": "burn"})
        import render as _r
        ok &= check("frame tiktok 1080x1920", _r.probe_size(burned) == (1080, 1920), str(_r.probe_size(burned)))
        ok &= check("nama file memuat tag frame", "[tiktok]" in burned.name, burned.name)
        ok &= check("bahasa auto -> Whisper language=None", calls.get("language") is None, str(calls))
        ok &= check("audio ke Whisper = durasi klip", abs(calls.get("wav_dur", 0) - 10) < 0.3, f"{calls.get('wav_dur'):.2f}s")
        ok &= check("bahasa terdeteksi dilaporkan", any("terdeteksi 'id'" in m.get("message", "") for m in msgs if m.get("status") == "message"))

        def gray(path, t):
            r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                                "-vf", "scale=108:192,format=gray", "-f", "rawvideo", "-"], capture_output=True)
            return r.stdout
        def mad(a, b): return sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        d_in, d_out = mad(gray(plain, 2.0), gray(burned, 2.0)), mad(gray(plain, 6.0), gray(burned, 6.0))
        ok &= check("subtitle tampil saat cue (detik 2)", d_in > 0.5, f"selisih={d_in:.2f}")
        ok &= check("tidak ada subtitle di luar cue (detik 6)", d_out < 0.3, f"selisih={d_out:.2f}")

        soft = clip_from_formats("uji", vf, af, "0:20", "0:30", tmp / "o8c",
                                 look={"frame": "original", "subs": "whisper", "sub_mode": "soft"})
        codecs = sh("ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(soft)).stdout.split()
        ok &= check("subtitle soft = track mov_text", "mov_text" in codecs, ",".join(codecs))
        side = clip_from_formats("uji", vf, af, "0:20", "0:30", tmp / "o8d",
                                 look={"frame": "square", "subs": "whisper", "sub_mode": "srt"})
        srt = side.with_suffix(".srt")
        ok &= check("mode srt: file .srt di samping video", srt.exists() and "halo semua" in srt.read_text(), srt.name)
        ok &= check("frame square 1080x1080", _r.probe_size(side) == (1080, 1080))
        mp3 = clip_from_formats("uji", None, af, "0:20", "0:30", tmp / "o8e", look={"subs": "whisper"})
        ok &= check("audio saja + subs -> mp3 + srt", mp3.suffix == ".mp3" and mp3.with_suffix(".srt").exists())

        # whisper tidak terpasang: 'whisper' gagal tegas, 'auto' tetap membuat klip + peringatan
        sys.modules["faster_whisper"] = None
        subtitles._model.cache_clear()
        try:
            clip_from_formats("uji", vf, af, "0:20", "0:30", tmp / "o8f", look={"subs": "whisper"})
            ok &= check("whisper tidak terpasang -> error jelas", False)
        except subtitles.SubtitleError as e:
            ok &= check("whisper tidak terpasang -> error jelas", "faster-whisper" in str(e))
        msgs2 = []
        out_auto = clip_from_formats("uji", vf, af, "0:20", "0:30", tmp / "o8g", progress=msgs2.append, look={"subs": "auto"})
        ok &= check("subs=auto gagal -> klip tetap jadi + peringatan",
                    out_auto.exists() and any(m.get("level") == "warning" for m in msgs2 if m.get("status") == "message"))
        sys.modules.pop("faster_whisper", None)

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
