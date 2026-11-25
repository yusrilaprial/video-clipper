"""Pengelolaan cookies YouTube yang diunggah lewat web (format Netscape cookies.txt).

File disimpan di config.cookies_upload (di Docker: /data/cookies.txt, ikut volume) dengan izin 0600.
Isi cookies TIDAK pernah dikirim balik ke browser; hanya ringkasan status.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

import config

MAX_BYTES = 1024 * 1024
HEADER = "# Netscape HTTP Cookie File"
LOGIN_COOKIES = {"SID", "__Secure-3PSID", "__Secure-1PSID", "LOGIN_INFO", "SAPISID", "__Secure-3PAPISID"}
_DOMAIN = re.compile(r"(^|\.)(youtube\.com|google\.com|youtube-nocookie\.com)$", re.I)


class CookieError(ValueError):
    """File cookies tidak valid (pesan aman ditampilkan ke pengguna)."""


def managed_path() -> Path:
    return Path(config.get().cookies_upload)


def inspect(text: str) -> dict:
    """Validasi + ringkasan. Raise CookieError bila bukan cookies.txt Netscape yang berguna."""
    body = text.lstrip("\ufeff").strip()
    if not body:
        raise CookieError("File kosong.")
    if body[0] in "[{":
        raise CookieError("Format JSON tidak didukung. Ekspor dalam format Netscape (cookies.txt).")
    total = yt = 0
    login = False
    exp_login: list[int] = []
    now = int(time.time())
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) != 7:
            continue
        domain, _, _, _, expires, name, _ = f
        total += 1
        if _DOMAIN.search(domain.lstrip(".")):
            yt += 1
            if name in LOGIN_COOKIES:
                login = True
                try:
                    e = int(float(expires))
                    if e > 0:
                        exp_login.append(e)
                except ValueError:
                    pass
    if total == 0:
        raise CookieError("Tidak ada cookie yang terbaca. Pastikan formatnya Netscape cookies.txt (kolom dipisah tab).")
    if yt == 0:
        raise CookieError("Tidak ada cookie youtube.com / google.com di file ini. Ekspor saat membuka YouTube.")
    warnings = []
    if not login:
        warnings.append("Tidak ada cookie login (mis. SID, __Secure-3PSID). Pastikan Anda sudah login ke YouTube saat mengekspor.")
    elif exp_login and max(exp_login) < now:
        warnings.append("Cookie login di file ini sudah kedaluwarsa. Ekspor ulang dari sesi yang baru.")
    return {"count": total, "youtube": yt, "login": login,
            "expires": min(exp_login) if exp_login else None, "warnings": warnings}


def normalize(text: str) -> str:
    """Pastikan header Netscape ada (diwajibkan yt-dlp) dan akhir baris seragam."""
    body = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    first = body.split("\n", 1)[0].strip()
    if not (first.startswith("# Netscape HTTP Cookie File") or first.startswith("# HTTP Cookie File")):
        body = HEADER + "\n" + body
    return body + "\n"


def save(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise CookieError("File terlalu besar (maksimal 1 MB).")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CookieError("File bukan teks UTF-8.") from None
    info = inspect(text)
    path = managed_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(normalize(text))
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return info


def delete() -> bool:
    p = managed_path()
    if p.is_file():
        p.unlink()
        return True
    return False


def active_source() -> Optional[str]:
    """'upload' | 'file' | 'browser' | None  (urutan prioritas sama dengan yang dipakai yt-dlp)."""
    c = config.get()
    if managed_path().is_file():
        return "upload"
    if c.cookies_file:
        return "file"
    if c.cookies_from_browser:
        return "browser"
    return None


def status(can_manage: bool) -> dict:
    src = active_source()
    out = {"configured": src is not None, "source": src, "can_manage": can_manage, "uploaded": src == "upload"}
    if src == "upload":
        p = managed_path()
        out["updated"] = int(p.stat().st_mtime)
        try:
            out.update({k: v for k, v in inspect(p.read_text(encoding="utf-8")).items()})
        except (CookieError, OSError):
            out["warnings"] = ["File cookies yang tersimpan tidak terbaca. Unggah ulang."]
    return out
