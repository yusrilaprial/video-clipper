"""Terjemahkan pesan error yt-dlp/YouTube menjadi penjelasan + saran yang bisa ditindaklanjuti."""
from __future__ import annotations

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_PREFIX = re.compile(r"^\s*(ERROR:\s*)?(\[[^\]]+\]\s*[\w\-]*:?\s*)?", re.I)

# (kode, pola regex, pesan, saran bila cookies belum ada, saran bila cookies sudah ada)
_RULES = [
    ("bot_check", r"confirm you.{0,4}re not a bot|not a bot",
     "YouTube meminta verifikasi anti-bot untuk server ini. Ini terjadi pada IP server/datacenter dan bukan karena link Anda salah.",
     "Unggah cookies YouTube lewat tombol Cookies di pojok kanan atas. Jika tetap gagal, pakai proxy (CLIPPER_PROXY).",
     "Cookies yang tersimpan ditolak atau sudah kedaluwarsa. Ekspor ulang dari jendela privat yang baru lalu unggah lagi."),
    ("age", r"confirm your age|age-restricted|inappropriate for some users",
     "Video ini dibatasi usia.",
     "Unggah cookies dari akun YouTube yang sudah login dan terverifikasi usia (tombol Cookies).",
     "Pastikan akun pemilik cookies bisa menonton video ini di browser (sudah terverifikasi usia)."),
    ("members", r"members-only|join this channel|available to this channel's members",
     "Video ini khusus anggota (member) kanal.",
     "Unggah cookies dari akun yang menjadi member kanal tersebut (tombol Cookies).",
     "Pastikan akun pemilik cookies masih menjadi member kanal ini."),
    ("private", r"private video",
     "Video ini privat.", "Hanya pemilik video yang bisa mengaksesnya. Unggah cookies dari akun yang punya akses.",
     "Pastikan akun pemilik cookies punya akses ke video privat ini."),
    ("geo", r"available in your country|geo.?restrict|not available from your location",
     "Video ini diblokir di negara lokasi server.", "Gunakan proxy di negara yang diizinkan (CLIPPER_PROXY).",
     "Gunakan proxy di negara yang diizinkan (CLIPPER_PROXY)."),
    ("upcoming", r"live event will begin|premieres in|this live event|is upcoming",
     "Stream/premiere ini belum dimulai.", "Coba lagi setelah stream berjalan.", "Coba lagi setelah stream berjalan."),
    ("rate_limit", r"http error 429|too many requests|rate.?limit",
     "YouTube membatasi permintaan dari IP ini (terlalu banyak permintaan).",
     "Tunggu beberapa menit lalu coba lagi, atau pakai proxy (CLIPPER_PROXY).",
     "Tunggu beberapa menit lalu coba lagi, atau pakai proxy (CLIPPER_PROXY)."),
    ("unavailable", r"video unavailable|this video is (not available|no longer available)|has been removed|account.*terminated",
     "Video tidak tersedia (dihapus, disembunyikan, atau link salah).", "Periksa kembali link-nya di browser.",
     "Periksa kembali link-nya di browser."),
    ("format", r"requested format is not available|no video formats found",
     "Format/kualitas yang diminta tidak tersedia untuk video ini.",
     "Coba kualitas lain (mis. Terbaik) atau perbarui yt-dlp.", "Coba kualitas lain (mis. Terbaik) atau perbarui yt-dlp."),
    ("network", r"unable to download webpage|name resolution|getaddrinfo|timed out|connection (refused|reset)|network is unreachable",
     "Server tidak bisa menghubungi YouTube (masalah jaringan).", "Periksa koneksi internet/DNS server atau pengaturan proxy.",
     "Periksa koneksi internet/DNS server atau pengaturan proxy."),
]


def clean(raw: str) -> str:
    """Hilangkan kode warna ANSI dan awalan 'ERROR: [youtube] id:' dari pesan yt-dlp."""
    t = _ANSI.sub("", str(raw)).strip()
    return _PREFIX.sub("", t, count=1).strip() or t


def explain(raw: str, cookies_active: bool) -> dict:
    """Return {code, message, hint, detail}. code='other' bila tidak dikenali (message = pesan asli yang dibersihkan)."""
    detail = _ANSI.sub("", str(raw)).strip()
    text = detail.lower()
    for code, pat, msg, hint_no, hint_yes in _RULES:
        if re.search(pat, text, re.I):
            return {"code": code, "message": msg, "hint": hint_yes if cookies_active else hint_no, "detail": detail}
    return {"code": "other", "message": clean(raw), "hint": "", "detail": detail}
