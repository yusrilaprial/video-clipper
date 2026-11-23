"""Uji subtitle, frame, dan render alur video biasa.  Jalankan: python tests/test_subs_frames.py  (butuh ffmpeg)"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402
import subtitles as S  # noqa: E402
import vtt_server  # noqa: E402
from clipper import resolve_look  # noqa: E402


class ParserTest(unittest.TestCase):
    def test_youtube_auto_vtt_dedupe(self):
        cues = S.parse_vtt(vtt_server.VTT)
        self.assertEqual([c.text for c in cues],
                         ["halo semua selamat", "datang di video ini", "kita akan belajar memotong video"])
        self.assertEqual((cues[0].start, cues[0].end), (0.0, 1.5))

    def test_manual_vtt_with_hours_and_multiline(self):
        v = "WEBVTT\n\n1\n00:01:00.000 --> 00:01:02.500\nBaris satu\nbaris dua\n\n2\n01:00:00.000 --> 01:00:01.000\n<i>Miring</i>\n"
        c = S.parse_vtt(v)
        self.assertEqual(c[0].text, "Baris satu baris dua")
        self.assertEqual(c[1].start, 3600.0)
        self.assertEqual(c[1].text, "Miring")

    def test_clip_and_shift(self):
        cues = [S.Cue(0, 5, "a"), S.Cue(8, 12, "b"), S.Cue(20, 25, "c")]
        out = S.clip_cues(cues, 10, 21)
        self.assertEqual([(c.start, c.end, c.text) for c in out], [(0.0, 2.0, "b"), (10.0, 11.0, "c")])

    def test_srt_format(self):
        self.assertEqual(S.to_srt([S.Cue(3661.5, 3662.0, "x")]), "1\n01:01:01,500 --> 01:01:02,000\nx\n\n")

    def test_wrap_splits_long_cue_proportionally(self):
        long = S.Cue(0, 10, " ".join(["kata"] * 40))
        out = S.wrap_cues([long], 20)
        self.assertGreater(len(out), 2)
        self.assertAlmostEqual(out[0].start, 0)
        self.assertAlmostEqual(out[-1].end, 10)
        self.assertTrue(all(len(c.text) <= 40 for c in out))
        self.assertTrue(all(a.end <= b.start + 1e-9 for a, b in zip(out, out[1:])))

    def test_select_captions(self):
        v = lambda u: [{"ext": "json3", "url": "j"}, {"ext": "vtt", "url": u}]  # noqa: E731
        info = {"language": "id", "subtitles": {"en": v("m-en"), "live_chat": v("chat")},
                "automatic_captions": {"id": v("a-id"), "id-orig": v("a-id-orig"), "ja": v("a-ja")}}
        self.assertEqual(S.select_captions(info, "auto"), ("id", "otomatis", "a-id"))  # bahasa asli video
        self.assertEqual(S.select_captions(info, "en"), ("en", "manual", "m-en"))      # manual diutamakan
        self.assertEqual(S.select_captions(info, "ja")[2], "a-ja")
        self.assertIsNone(S.select_captions(info, "fr"))
        self.assertIsNone(S.select_captions({"subtitles": {"live_chat": v("c")}}, "auto"))
        no_orig = {"automatic_captions": {"en-orig": v("o"), "en": v("e")}}
        self.assertEqual(S.select_captions(no_orig, "auto")[0], "en-orig")


class WhisperAudioTest(unittest.TestCase):
    def test_wav_loaded_as_float32_array_without_pyav(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as t:
            ok = Path(t) / "ok.wav"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=2", "-ac", "1",
                            "-ar", "16000", "-c:a", "pcm_s16le", str(ok)], check=True)
            a = S._load_wav(ok)
            self.assertIsInstance(a, np.ndarray)
            self.assertEqual((a.dtype, len(a)), (np.float32, 32000))
            self.assertTrue(-1.0 <= a.min() and a.max() <= 1.0 and a.max() > 0.1)  # sine ffmpeg: amplitudo 0.125
            bad = Path(t) / "bad.wav"  # stereo 44.1 kHz ditolak
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=1", "-ac", "2",
                            "-ar", "44100", "-c:a", "pcm_s16le", str(bad)], check=True)
            with self.assertRaises(S.SubtitleError):
                S._load_wav(bad)
            junk = Path(t) / "bukan-wav.wav"
            junk.write_bytes(b"xx")
            with self.assertRaises(S.SubtitleError):
                S._load_wav(junk)

    def test_pyav_error_gets_actionable_hint(self):
        import types
        from unittest import mock

        class Boom:
            def transcribe(self, *a, **k):
                raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")

        with tempfile.TemporaryDirectory() as t:
            wav = Path(t) / "a.wav"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=1", "-ac", "1",
                            "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], check=True)
            with mock.patch.object(S, "_model", lambda *a, **k: Boom()):
                with self.assertRaises(S.SubtitleError) as cm:
                    S.transcribe(wav, "auto")
        self.assertIn("av>=12,<19", str(cm.exception))


class FrameTest(unittest.TestCase):
    def test_resolve(self):
        r = render.resolve_frame
        self.assertIsNone(r("original"))
        self.assertEqual(r("tiktok"), (1080, 1920))
        self.assertEqual(r("IG"), (1080, 1350))
        self.assertEqual(r("youtube"), (1920, 1080))
        self.assertEqual(r("3:4"), (1080, 1440))
        self.assertEqual(r("720x1281"), (720, 1282))  # dibulatkan ke genap
        for bad in ("bogus", "10:1", "5000x5000", "0:5"):
            with self.assertRaises(ValueError):
                r(bad)

    def test_look_validation(self):
        self.assertEqual(resolve_look({"frame": "tiktok", "subs": "auto"})["sub_lang"], "auto")
        for bad in ({"fit": "x"}, {"subs": "x"}, {"sub_mode": "x"}, {"focus": 2}, {"sub_lang": "bahasa indonesia"},
                    {"frame": "nope"}):
            with self.assertRaises(ValueError):
                resolve_look(bad)


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        cls.src = t / "src.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=5",
                        "-f", "lavfi", "-i", "sine=f=440:d=5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(cls.src)], check=True)
        cls.srv = vtt_server.start()
        port = cls.srv.server_address[1]
        cls.info = {"language": "id", "automatic_captions": {"id": [{"ext": "vtt", "url": f"http://127.0.0.1:{port}/a.vtt"}]}}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def work(self, name):
        import shutil
        p = Path(self.tmp.name) / f"{name}.mp4"
        shutil.copy(self.src, p)
        return p

    def test_all_fit_frame_combinations_have_exact_size(self):
        for fit in ("blur", "crop", "pad"):
            for frame in ("tiktok", "instagram", "youtube", "square", "3:4", "cinema"):
                with self.subTest(fit=fit, frame=frame):
                    size = render.resolve_frame(frame)
                    out = Path(self.tmp.name) / f"o_{fit}_{frame.replace(':', '')}.mp4"
                    g = render.video_graph("0:v:0", "vout", size, fit, 0.5, None)
                    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(self.src), "-filter_complex", g,
                                    "-map", "[vout]", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
                    self.assertEqual(render.probe_size(out), size)

    def test_focus_changes_crop_region(self):
        outs = []
        for f in (0.0, 1.0):
            out = Path(self.tmp.name) / f"focus{f}.mp4"
            g = render.video_graph("0:v:0", "vout", (360, 360), "crop", f, None)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(self.src), "-filter_complex", g, "-map", "[vout]",
                            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True)
            r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(self.src), "-filter_complex", g, "-map", "[vout]",
                                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
            outs.append(r.stdout)
        self.assertNotEqual(outs[0], outs[1])

    def test_vod_postprocess_burn_with_youtube_captions(self):
        out = render.postprocess_vod(self.work("a"), self.info, 0.0, 5.0, Path(self.tmp.name) / "out1",
                                     frame="tiktok", fit="blur", focus=0.5, subs="auto", lang="auto",
                                     sub_mode="burn", audio_only=False)
        self.assertEqual(render.probe_size(out), (1080, 1920))
        self.assertIn("[tiktok]", out.name)

    def test_vod_postprocess_srt_shifted_to_clip_start(self):
        out = render.postprocess_vod(self.work("b"), self.info, 1.51, 5.0, Path(self.tmp.name) / "out2",
                                     frame="original", fit="blur", focus=0.5, subs="youtube", lang="id",
                                     sub_mode="srt", audio_only=False)
        srt = out.with_suffix(".srt").read_text()
        self.assertIn("00:00:00,000 --> 00:00:01,490", srt)  # cue 1.51-3.0 digeser ke awal klip
        self.assertEqual(render.probe_size(out), (640, 360))  # original tidak diubah

    def test_vod_no_changes_just_moves_file(self):
        src = self.work("c")
        out = render.postprocess_vod(src, {}, 0, 5, Path(self.tmp.name) / "out3", frame="original", fit="blur",
                                     focus=0.5, subs="off", lang="auto", sub_mode="burn", audio_only=False)
        self.assertTrue(out.exists())
        self.assertFalse(src.exists())

    def test_explicit_youtube_without_captions_raises(self):
        with self.assertRaises(S.SubtitleError):
            render.postprocess_vod(self.work("d"), {}, 0, 5, Path(self.tmp.name) / "out4", frame="original",
                                   fit="blur", focus=0.5, subs="youtube", lang="auto", sub_mode="burn", audio_only=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
