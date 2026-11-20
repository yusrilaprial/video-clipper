# Video Clipper

Potong video dari link YouTube (dan platform lain yang didukung yt-dlp: TikTok, Instagram, X/Twitter, Facebook, Vimeo, dll.)
dengan memasukkan waktu mulai & akhir, lalu langsung dirender ke MP4 (atau MP3).

Hanya bagian yang dipotong yang diunduh, jadi video panjang tidak perlu diunduh penuh.

## Persyaratan
1. Python 3.9+
2. **ffmpeg** terpasang dan ada di PATH
   - Windows: `winget install ffmpeg`
   - macOS: `brew install ffmpeg`
   - Ubuntu/Debian: `sudo apt install ffmpeg`

## Instalasi
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Cara pakai

### A. Web UI
```bash
python app.py
```
Buka http://127.0.0.1:5000, tempel link, isi mulai/akhir, klik **Potong & Render**.

### B. CLI
```bash
python cli.py "https://www.youtube.com/watch?v=XXXX" -s 1:30 -e 2:45
python cli.py URL -s 00:10 -e 00:40 -q 720 -o hasil
python cli.py URL --clip 0:10-0:40 --clip 5:00-5:30     # beberapa klip
python cli.py URL -s 1:00 -e 1:30 --fast                # cepat, potong di keyframe
python cli.py URL -s 1:00 -e 1:30 -q audio              # hanya audio mp3
```
Format waktu: `90`, `1:30`, `01:02:03`, `1:30.5`, `1m30s`, `1h2m3s`.

Hasil tersimpan di folder `output/`.

## Livestream yang masih berjalan (mode live)

Aplikasi mendeteksi otomatis livestream (`--mode auto`). Untuk live, aplikasi **tidak** mengunduh seluruh stream:
waktu mulai/akhir dipetakan ke nomor segmen YouTube (`sq=N`), lalu hanya segmen itu yang diunduh
(ditambah header sq=0 dan 1 segmen margin di tiap sisi), kemudian dipotong presisi dan dirender oleh ffmpeg.

```bash
python cli.py "https://www.youtube.com/watch?v=LIVE_ID" -s 1:20:00 -e 1:21:30      # klip 1.5 menit dari stream yang masih live
python cli.py "LINK_LIVE" -s -1:30 -e 0:00                                        # 1 mnt 30 dtk terakhir sampai sekarang
python cli.py "LINK_LIVE" -s -5:00 -e -4:00                                       # 5 mnt lalu sampai 4 mnt lalu
python cli.py "LINK_LIVE" --clip -10:00..-9:00 --clip -2:00..0:00                 # beberapa klip relatif
python cli.py "LINK_LIVE" -s 1:20:00 -e 1:21:30 --test                            # uji dulu: hanya 10 detik (hemat kuota)
python cli.py "LINK_LIVE" -s 1:20:00 -e 1:21:30 --mode live -q 720
```

Waktu relatif (khusus live): awalan `-` berarti "sekian yang lalu", dan `0:00` / `now` berarti sekarang.
Keduanya harus relatif atau keduanya absolut. "Sekarang" = akhir segmen lengkap terakhir, yaitu sekitar 2-5 detik
sebelum posisi live paling ujung (segmen terakhir belum selesai ditulis YouTube).

Syarat & batasan:
- DVR stream harus aktif (di player bisa digeser mundur). Tanpa DVR, bagian lampau memang tidak tersedia.
- Waktu mengikuti timeline di player. Jendela mundur dibatasi YouTube per stream; di luar itu muncul error "Segmen tidak tersedia".
- Waktu akhir tidak boleh melewati live edge; pesan error menyebutkan panjang stream saat ini.
- Mode live selalu merender ulang (libx264 + AAC), jadi lebih lambat dari mode `--fast` pada video biasa.
- Estimasi data: sebesar klip + sekitar 3 segmen tambahan (2 margin + sq=0).

Uji offline tanpa internet (server YouTube tiruan, butuh ffmpeg): `python tests/test_live_local.py`

## Catatan
- Mode presisi (default) memotong tepat di detik yang diminta; `--fast` jauh lebih cepat tapi bisa meleset beberapa detik.
- Jika YouTube meminta login/anti-bot, tambahkan cookies browser: set opsi `cookiesfrombrowser` di `clipper.py`
  (mis. `opts["cookiesfrombrowser"] = ("chrome",)`).
- Perbarui yt-dlp secara berkala: `pip install -U yt-dlp` (YouTube sering berubah).
- Gunakan hanya untuk konten yang kamu punya hak/izin memakainya.
