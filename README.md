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

## Konfigurasi (.env / environment variable)

Salin `.env.example` menjadi `.env`, lalu hapus tanda `#` pada baris yang ingin diubah. Environment variable asli
(`export` / `set`) selalu menang atas `.env`. Nilai yang salah ditolak saat start dengan pesan yang jelas.
Cek hasil akhirnya dengan `python cli.py --show-config`.

| Variabel | Bawaan | Fungsi |
|---|---|---|
| `CLIPPER_OUTPUT_DIR` | `output` | Folder hasil klip |
| `CLIPPER_QUALITY` | `best` | `best`, `1080`, `720`, `480`, `360`, `audio` (mp3) |
| `CLIPPER_MODE` | `auto` | `auto` (deteksi live), `live`, `vod` |
| `CLIPPER_PRECISE` | `true` | Potong presisi pada video biasa (`false` = cepat, bisa meleset) |
| `CLIPPER_TEST_SECONDS` | `10` | Lama klip pada `--test` |
| `CLIPPER_TITLE_MAX` | `80` | Panjang maksimal judul di nama file |
| `CLIPPER_FFMPEG_PATH` / `CLIPPER_FFPROBE_PATH` | dari PATH | Folder atau path ffmpeg jika tidak ada di PATH |
| `CLIPPER_COOKIES_UPLOAD_PATH` / `CLIPPER_YT_PLAYER_CLIENTS` | di samping output / kosong | Lokasi cookies hasil unggahan web; client pemutar YouTube (lanjutan) |
| `CLIPPER_PROXY` | kosong | Proxy untuk yt-dlp dan unduhan segmen live |
| `CLIPPER_COOKIES_FILE` / `CLIPPER_COOKIES_FROM_BROWSER` | kosong | Cookies untuk video yang meminta login (`chrome`, `firefox:PROFIL`, dst.) |
| `CLIPPER_FRAME` / `CLIPPER_FIT` / `CLIPPER_FOCUS` | `original` / `blur` / `0.5` | Frame hasil, cara mengisi frame, posisi potongan |
| `CLIPPER_SUBS` / `CLIPPER_SUB_LANG` / `CLIPPER_SUB_MODE` | `off` / `auto` / `burn` | Subtitle: sumber, bahasa, jenis |
| `CLIPPER_SUB_SCALE` / `CLIPPER_SUB_FONT` | `1.0` / kosong | Ukuran teks subtitle dan nama font |
| `CLIPPER_WHISPER_MODEL` / `_DEVICE` / `_COMPUTE` / `_DIR` | `base` / `auto` / `auto` / kosong | Pengaturan Whisper |
| `CLIPPER_AUTH_USER` / `CLIPPER_AUTH_PASSWORD` | kosong | Login web UI (wajib di server) |
| `CLIPPER_SECRET_KEY` / `CLIPPER_COOKIE_SECURE` / `CLIPPER_TRUST_PROXY` | kosong / `false` / `false` | Kunci sesi, cookie khusus HTTPS, IP asli di belakang proxy |
| `CLIPPER_LOGIN_MAX_ATTEMPTS` / `CLIPPER_LOGIN_LOCK_SECONDS` | `5` / `300` | Batas gagal login sebelum IP dikunci sementara |
| `CLIPPER_MAX_JOBS` / `CLIPPER_MAX_CLIP_SECONDS` / `CLIPPER_RETENTION_HOURS` | `2` / `0` / `0` | Batas render bersamaan, durasi klip, masa simpan hasil |
| `CLIPPER_ALLOWED_DOMAINS` | kosong | Daftar domain yang boleh diunduh (pisahkan koma) |
| `CLIPPER_LIVE_WORKERS` | `4` | Unduhan segmen paralel (1-16) |
| `CLIPPER_LIVE_MARGIN` | `1` | Segmen cadangan di tiap sisi rentang (0-5) |
| `CLIPPER_LIVE_RETRIES` / `CLIPPER_HTTP_TIMEOUT` | `4` / `30` | Percobaan ulang per segmen / batas waktu HTTP (detik) |
| `CLIPPER_VIDEO_PRESET` / `CLIPPER_VIDEO_CRF` | `veryfast` / `20` | Pengaturan render x264 pada mode live |
| `CLIPPER_AUDIO_BITRATE` | `192k` | Bitrate audio (mp3/aac) |
| `CLIPPER_HOST` / `CLIPPER_PORT` | `127.0.0.1` / `5000` | Alamat web UI |
| `CLIPPER_ENV_FILE` | kosong | Path file `.env` lain |

Opsi CLI (`-q`, `-o`, `--mode`, `--fast`) selalu menimpa nilai dari `.env`. Jangan commit `.env` atau `cookies.txt`
(sudah ada di `.gitignore`).

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

## Antarmuka web

Jalankan server lalu buka http://127.0.0.1:5000 (atau lewat Docker, lihat bagian deploy).

- **Responsif:** satu kolom di ponsel dengan bilah aksi tetap di bawah (tombol "Potong" selalu terjangkau), dua kolom di desktop
  dengan ringkasan yang menempel di sisi kanan. Target ketuk 44px di layar sentuh, tanpa scroll horizontal dari lebar 320px sampai 1920px.
- **Tema gelap/terang** mengikuti perangkat, bisa diganti dengan tombol di bar atas (tersimpan).
- **Link otomatis dibaca:** tempel link (atau tombol clipboard) lalu pratinjau muncul (thumbnail, judul, durasi, lencana LIVE).
- **Waktu dengan slider** untuk video biasa (tersinkron dengan kolom mulai/akhir), chip panjang cepat (15 dtk ... 5 mnt), dan chip
  "N menit terakhir" untuk livestream. Format waktu salah ditandai langsung di kolom, tanpa popup.
- **Frame berupa kartu visual** (TikTok, Reels, Shorts, IG Feed, Persegi, YouTube, X, Sinema, Kustom). Pilihan "isi frame", fokus,
  bahasa, dan jenis subtitle hanya muncul bila relevan.
- **Ringkasan** pilihan selalu terlihat; tombol baru aktif setelah link dan waktu valid.
- **Progres bertahap** (Antre, Unduh, Proses) dengan persen unduhan, catatan (bahasa terdeteksi, peringatan), dan waktu berjalan.
- **Hasil** langsung bisa diputar di halaman (dengan seek), lalu unduh video dan `.srt`.
- **Riwayat** 8 klip terakhir di browser ini (putar, unduh, hapus); klip yang sudah dibersihkan server ditandai kedaluwarsa.
- Pengaturan terakhir (frame, subtitle, kualitas, dll.) diingat. Pintasan: `Ctrl/Cmd + Enter` untuk memulai.

## Error "Sign in to confirm you're not a bot"

Pesan ini berasal dari **YouTube**, bukan bug aplikasi dan bukan karena link salah. YouTube makin sering menganggap permintaan dari
IP server/datacenter (VPS, cloud, Docker di hosting) sebagai bot, sedangkan dari komputer rumahan biasanya lolos. Saat link dimasukkan,
aplikasi menampilkan penjelasan dan langkah perbaikan; pesan asli ada di "Detail teknis".

Perbaikan, dari yang paling andal:
1. **Unggah cookies** dari sesi YouTube yang sudah login: klik tombol **Cookies** di pojok kanan atas web UI, ikuti petunjuk di dialog,
   lalu unggah `cookies.txt`. Video langsung dibaca ulang. (Butuh login web aktif, atau server hanya terbuka di localhost.)
   - Ekspor dari **jendela privat**: login ke YouTube, buka `youtube.com/robots.txt` di tab yang sama, ekspor cookies `youtube.com`
     (format Netscape, mis. ekstensi "Get cookies.txt LOCALLY"), lalu **tutup jendela privat**. Ini mencegah YouTube memutar ulang cookies tersebut.
   - **Pakai akun khusus**, bukan akun utama: YouTube dapat membatasi akun yang dipakai untuk unduhan otomatis.
   - Cookies kedaluwarsa atau ditolak? Ekspor ulang dengan cara yang sama, lalu unggah lagi.
   - Tanpa web UI: letakkan file di server dan isi `CLIPPER_COOKIES_FILE`, atau di Docker: `docker cp cookies.txt video-clipper:/data/cookies.txt`.
2. **Proxy** dengan IP yang lebih "bersih" (mis. residensial): `CLIPPER_PROXY=http://user:pass@host:port`.
3. Lanjutan: `CLIPPER_YT_PLAYER_CLIENTS=tv,web_safari` memaksa client pemutar tertentu. Tidak ada jaminan karena YouTube sering berubah.
   Selalu perbarui yt-dlp: `pip install -U yt-dlp` (Docker: `docker compose build --no-cache`).

Keamanan cookies: file disimpan dengan izin 0600, isinya tidak pernah dikirim balik ke browser, dan setiap unduhan memakai salinan
sementara (yt-dlp menulis ulang file cookies saat selesai, sehingga salinan mencegah job bersamaan saling menimpa).
Pesan lain (video privat, dibatasi usia, khusus member, diblokir negara, stream belum mulai, dll.) juga dijelaskan dengan saran yang sesuai.

## Frame untuk TikTok, Instagram, YouTube, dll.

```bash
python cli.py URL -s 1:30 -e 2:00 --frame tiktok                 # 9:16 (1080x1920), latar buram
python cli.py URL -s 1:30 -e 2:00 --frame instagram --fit crop   # 4:5 feed, potong tengah
python cli.py URL -s 1:30 -e 2:00 --frame reels --fit crop --focus 0.3   # fokus ke sisi kiri
python cli.py URL -s 1:30 -e 2:00 --frame 3:4                    # rasio bebas; atau ukuran: --frame 720x1280
python cli.py --list-frames                                      # semua preset
```
Preset: `original`, `tiktok` / `reels` / `shorts` / `story` (9:16), `instagram` / `ig-feed` (4:5), `square` (1:1),
`youtube` (16:9), `twitter` / `x` (16:9 720p), `cinema` (21:9), serta rasio `A:B` atau ukuran `LEBARxTINGGI`.

`--fit` menentukan cara mengisi frame: **blur** (video utuh di tengah, latar buram; tidak ada yang terpotong),
**crop** (potong tengah, `--focus 0-1` menggeser posisi), **pad** (bingkai hitam). Kualitas sumber memengaruhi hasil:
untuk frame 1080p pakai `-q 1080`. Frame selain `original` selalu merender ulang video.

## Subtitle (deteksi bahasa otomatis)

```bash
python cli.py URL -s 1:30 -e 2:00 --subs auto                    # caption YouTube bila ada, jika tidak pakai Whisper
python cli.py URL -s 1:30 -e 2:00 --subs whisper --sub-lang auto # transkripsi AI, bahasa terdeteksi otomatis
python cli.py URL -s 1:30 -e 2:00 --subs youtube --sub-lang en   # caption YouTube bahasa Inggris
python cli.py URL -s 1:30 -e 2:00 --subs auto --frame tiktok --sub-mode soft   # track yang bisa dimatikan
python cli.py URL -s 1:30 -e 2:00 --subs auto --sub-mode srt                   # file .srt terpisah
```
- `auto`: pakai caption YouTube (manual lebih dulu, lalu otomatis) bila ada; jika tidak, transkripsi Whisper pada klip.
  Jika keduanya gagal, klip tetap dibuat tanpa subtitle dan muncul peringatan. `youtube` / `whisper` yang gagal menghentikan proses.
- Bahasa: `auto` = bahasa asli video (caption YouTube) atau dideteksi Whisper dari ucapan; atau isi kode seperti `id`, `en`, `ja`.
  Bahasa yang terdeteksi ditampilkan setelah proses.
- Jika muncul error `open() got an unexpected keyword argument 'metadata_errors'`: itu ketidakcocokan PyAV (`av` 19+) dengan
  faster-whisper. Versi ini sudah menahan `av<19` dan mengirim audio ke Whisper sebagai array (tanpa PyAV). Di Docker, bangun ulang
  image: `docker compose build --no-cache && docker compose up -d`.
- Whisper bersifat opsional: `pip install -r requirements-whisper.txt`. Model diunduh sekali saat pertama dipakai
  (butuh internet; `base` sekitar 150 MB). Ganti ukuran model dengan `CLIPPER_WHISPER_MODEL` (`tiny` ... `large-v3`).
- Jenis: `burn` (tertanam; posisi otomatis menghindari area UI TikTok/Reels), `soft` (track mov_text), `srt` (file terpisah).
- Livestream umumnya belum punya caption YouTube, jadi `auto` akan memakai Whisper pada audio klip.
- Dengan subtitle aktif, pemotongan selalu presisi (`--fast` diabaikan) agar waktu subtitle sinkron.
- Teks non-Latin (Arab, Mandarin, Jepang, Korea, dll.) butuh font yang sesuai di sistem (di Docker: build arg `INSTALL_CJK_FONTS=true`).

## Deploy ke server (Docker)

```bash
cp .env.example .env        # isi minimal CLIPPER_AUTH_USER dan CLIPPER_AUTH_PASSWORD
docker compose up -d --build
docker compose logs -f
```
Web UI berjalan di `127.0.0.1:5000` pada server. Untuk akses dari internet, **pasang reverse proxy HTTPS** (jangan buka
port 5000 langsung). Contoh Caddy (HTTPS otomatis):
```
clipper.contoh.com {
    reverse_proxy 127.0.0.1:5000
}
```
Catatan penting:
- **Halaman login:** setelah `CLIPPER_AUTH_USER` / `CLIPPER_AUTH_PASSWORD` diisi, web UI menampilkan halaman login sendiri
  (bukan dialog bawaan browser): error tampil di dalam form, ada tampilkan/sembunyikan kata sandi, peringatan Caps Lock,
  "ingat saya 14 hari", tema gelap/terang otomatis, dan tombol **Keluar** di halaman utama. Sesi memakai cookie
  (HttpOnly, SameSite=Lax) dan form dilindungi token CSRF. Percobaan gagal dibatasi: 5 kali gagal berturut-turut mengunci IP
  itu 5 menit (`CLIPPER_LOGIN_MAX_ATTEMPTS`, `CLIPPER_LOGIN_LOCK_SECONDS`), dengan hitung mundur di layar.
  Skrip/curl tetap bisa memakai Basic Auth: `curl -u user:password ...`.
- **Di belakang reverse proxy HTTPS**, isi di `.env`: `CLIPPER_COOKIE_SECURE=true` dan `CLIPPER_TRUST_PROXY=true`.
  Tanpa `TRUST_PROXY`, semua pengunjung terlihat berasal dari IP proxy sehingga pembatasan login berlaku untuk semua orang sekaligus.
  Ganti password = semua sesi lama otomatis tidak berlaku.
- **Selalu isi login** (`CLIPPER_AUTH_USER` / `CLIPPER_AUTH_PASSWORD`). Tanpa itu siapa pun yang menemukan alamatnya dapat memakai
  CPU dan bandwidth server Anda. Disarankan juga `CLIPPER_ALLOWED_DOMAINS=youtube.com,youtu.be` agar server hanya mengunduh dari domain itu.
- Atur beban dengan `CLIPPER_MAX_JOBS` (render bersamaan; sisanya antre), `CLIPPER_MAX_CLIP_SECONDS` (bawaan compose 1800),
  dan `CLIPPER_RETENTION_HOURS` (hapus hasil otomatis, bawaan compose 24 jam).
- Container berjalan sebagai user non-root. Data (hasil klip dan model Whisper) ada di volume `clipper-data`.
- Hanya 1 proses gunicorn (status job ada di memori); jangan menambah `--workers`.
- Image tanpa Whisper (lebih kecil): ubah `WITH_WHISPER: "false"` di `docker-compose.yml`. GPU tidak disiapkan di image ini.
- Perbarui yt-dlp secara berkala: `docker compose build --no-cache && docker compose up -d`.
- Proxy/cookies untuk YouTube: isi `CLIPPER_PROXY` / `CLIPPER_COOKIES_FILE` di `.env` (mount file cookies ke container).
- Endpoint cek kesehatan: `GET /healthz`.

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

Uji offline tanpa internet (butuh ffmpeg): `python tests/test_live_local.py` (mode live), `python tests/test_subs_frames.py` (subtitle, frame, render), `python tests/test_web.py` (web app), `python tests/test_cookies.py` (cookies dan penerjemah error) dan `python tests/test_config.py` (konfigurasi).

## Catatan
- Mode presisi (default) memotong tepat di detik yang diminta; `--fast` jauh lebih cepat tapi bisa meleset beberapa detik.
- Jika YouTube meminta login/anti-bot, isi `CLIPPER_COOKIES_FROM_BROWSER=chrome` (atau `CLIPPER_COOKIES_FILE`) di `.env`.
- Perbarui yt-dlp secara berkala: `pip install -U yt-dlp` (YouTube sering berubah).
- Gunakan hanya untuk konten yang kamu punya hak/izin memakainya.
