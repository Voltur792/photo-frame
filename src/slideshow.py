from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

log = logging.getLogger("photo-frame")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}
ANIM_EXTS = {".gif", ".webp", ".apng"}
MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".apng": "image/apng",
    ".bmp": "image/bmp", ".mp4": "video/mp4", ".mov": "video/mp4",
    ".m4v": "video/mp4", ".webm": "video/webm",
}
MAX_PHOTOS = 1500              # потолок merged-списка, чтобы state не раздувался
MAX_INLINE_MEDIA = 12_000_000  # видео/анимация через bridge только до этого размера
CACHE_LIMIT_BYTES = 64 * 1024 * 1024  # сколько base64 держим в памяти плагина
MAX_FOLDERS = 12

TRANSITIONS = {"fade", "slide", "kenburns", "none"}
FITS = {"contain", "cover"}
FRAME_STYLES = {"transparent", "glass", "dark", "white", "gallery"}
CAPTIONS = {"none", "name", "date", "clock"}
VIDEO_MODES = {"once", "loop"}

CFG_DEFAULTS = {
    "folders": [],
    "recursive": False,
    "shuffle": False,
    "interval": 10,
    "transition": "fade",
    "fit": "contain",
    "frame": "glass",
    "padding": 14,
    "radius": 10,
    "caption": "none",
    "video_mode": "once",
}

def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "да", "вкл", "on"):
            return True
        if lowered in ("false", "0", "нет", "выкл", "off", ""):
            return False
    return default

def _as_int(value, default: int, lo: int, hi: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return min(max(number, lo), hi)

def _clean_folder(value) -> str:
    if isinstance(value, str):
        value = os.path.expanduser(value.strip().strip('"'))
        return value if len(value) <= 512 else ""
    return ""

def _parse_folders(raw) -> list[str]:
    result: list[str] = []
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    elif isinstance(raw, str):
        items = raw.replace(";", "\n").splitlines()
    else:
        items = []
    for item in items:
        folder = _clean_folder(item)
        if folder and folder not in result:
            result.append(folder)
    return result[:MAX_FOLDERS]

def normalize_config(raw) -> dict:

    cfg = dict(CFG_DEFAULTS)
    cfg["folders"] = []
    if not isinstance(raw, dict):
        return cfg
    folders = _parse_folders(raw.get("folders"))
    if not folders:
        legacy = _clean_folder(raw.get("folder"))
        if legacy:
            folders = [legacy]
    cfg["folders"] = folders
    cfg["recursive"] = _as_bool(raw.get("recursive"), cfg["recursive"])
    cfg["shuffle"] = _as_bool(raw.get("shuffle"), cfg["shuffle"])
    cfg["interval"] = _as_int(raw.get("interval"), cfg["interval"], 2, 3600)
    for key, allowed in (("transition", TRANSITIONS), ("fit", FITS),
                        ("frame", FRAME_STYLES), ("caption", CAPTIONS),
                        ("video_mode", VIDEO_MODES)):
        value = raw.get(key, cfg[key])
        if isinstance(value, str) and value in allowed:
            cfg[key] = value
    cfg["padding"] = _as_int(raw.get("padding"), cfg["padding"], 0, 80)
    cfg["radius"] = _as_int(raw.get("radius"), cfg["radius"], 0, 48)
    if cfg["frame"] == "transparent":
        cfg["padding"] = 0
    return cfg

def photo_id(folder_index: int, relpath: str) -> str:
    return f"{folder_index}::{relpath}"

def parse_photo_id(raw_id: str) -> tuple[int, str]:

    head, sep, rest = str(raw_id).partition("::")
    if not sep:
        return 0, str(raw_id)
    try:
        return int(head), rest
    except ValueError:
        return -1, rest

_scan_cache: "OrderedDict[tuple, tuple[float, list[dict]]]" = OrderedDict()
_scan_lock = threading.Lock()
_SCAN_TTL = 15.0

def scan_photos(cfg: dict, force: bool = False) -> tuple[list[dict], str]:

    folders = cfg["folders"]
    if not folders:
        return [], "Укажите папку(и) с фотографиями в настройках рамки"
    key = (tuple(os.path.realpath(f) for f in folders), bool(cfg["recursive"]))
    now = time.monotonic()
    if not force:
        with _scan_lock:
            hit = _scan_cache.get(key)
            if hit and now - hit[0] < _SCAN_TTL:
                return [dict(p) for p in hit[1]], ""
    photos, error = _scan_photos_uncached(cfg)
    if error:
        return [], error
    with _scan_lock:
        _scan_cache[key] = (now, photos)
        while len(_scan_cache) > 8:
            _scan_cache.popitem(last=False)
    return [dict(p) for p in photos], ""

def _kind_for(ext: str) -> str:
    return "video" if ext in VIDEO_EXTS else "image"

def _scan_photos_uncached(cfg: dict) -> tuple[list[dict], str]:
    photos: list[dict] = []
    missing: list[str] = []
    for index, folder in enumerate(cfg["folders"]):
        root = Path(folder)
        try:
            if not root.is_dir():
                missing.append(folder)
                continue
        except OSError:
            missing.append(folder)
            continue
        stack = [root]
        while stack:
            current = stack.pop(0)
            try:
                entries = sorted(os.scandir(current), key=lambda e: e.name.lower())
            except OSError:
                continue
            for entry in entries:
                name = entry.name
                if name.startswith("."):
                    continue
                try:
                    if entry.is_dir():
                        if cfg["recursive"]:
                            stack.append(Path(entry.path))
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    if entry.is_file() and (ext in IMAGE_EXTS or ext in VIDEO_EXTS):
                        stat_result = entry.stat()
                        photos.append({
                            "id": photo_id(index, os.path.relpath(entry.path, root)
                                           .replace("\\", "/")),
                            "abs": entry.path,
                            "name": os.path.splitext(name)[0],
                            "mtime": int(stat_result.st_mtime),
                            "size": int(stat_result.st_size),
                            "kind": _kind_for(ext),
                        })
                except OSError:
                    continue
            if len(photos) > MAX_PHOTOS:
                break
    if missing and len(missing) == len(cfg["folders"]):
        return [], "Папки не найдены: " + " ; ".join(missing)
    photos.sort(key=lambda p: p["id"].lower())
    return photos[:MAX_PHOTOS], ""

def resolve_photo(cfg: dict, raw_id: str) -> tuple[str, str]:

    index, rel = parse_photo_id(raw_id)
    if not raw_id or not rel or index < 0 or index >= len(cfg["folders"]):
        return "", "Кадр не найден: обновите список"
    root = Path(cfg["folders"][index]).resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return "", "Кадр недоступен: обновите список"
    return str(candidate), ""

def media_token(cfg: dict) -> str:
    return hashlib.sha1(("\n".join(cfg["folders"]) + "\0" + str(cfg["recursive"]))
                       .encode("utf-8", "replace")).hexdigest()[:16]

def media_urls(base: str, photos: list[dict]) -> None:

    from urllib.parse import quote
    for photo in photos:
        if not base:
            photo["url"] = ""
            continue
        ext = os.path.splitext(photo["id"])[1].lower()
        stage = "raw" if photo["kind"] == "video" or ext in ANIM_EXTS else "full"
        index, rel = parse_photo_id(photo["id"])
        tail = f"{stage}/{index}/" + "/".join(quote(part) for part in rel.split("/"))
        photo["url"] = f"{base}{tail}?v={photo['mtime']}"

def state_for(cfg: dict, force: bool = False, media_base: str = "") -> dict:
    photos, error = scan_photos(cfg, force=force)
    if error:
        return {"ok": False, "err": error, "count": 0}
    media_urls(media_base, photos)
    return {"ok": True, "count": len(photos),
            "photos": [{"id": p["id"], "name": p["name"], "mtime": p["mtime"],
                       "kind": p["kind"], "url": p.get("url", ""), "size": p["size"]}
                       for p in photos]}

_cache: "OrderedDict[tuple, tuple[str, int]]" = OrderedDict()
_cache_bytes = 0

def _cache_put(key: tuple, payload: str) -> None:
    global _cache_bytes
    _cache[key] = (payload, len(payload))
    _cache_bytes += len(payload)
    while _cache and _cache_bytes > CACHE_LIMIT_BYTES:
        _, (_, size) = _cache.popitem(last=False)
        _cache_bytes -= size

def _read_source(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise OSError(f"Не удалось прочитать файл: {exc}") from exc

def encode_bytes(path: str, max_side: int, quality: int = 82) -> tuple[str, bytes, str]:

    max_side = _as_int(max_side, 1600, 64, 2400)
    raw = _read_source(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gif" and len(raw) <= MAX_INLINE_MEDIA:
        return "image/gif", raw, ""
    try:
        from PIL import Image, ImageOps
        image = Image.open(io.BytesIO(raw))
        if image.format == "JPEG" and max(image.size) > max_side:
            image.draft("RGB", (max_side, max_side))
        image = ImageOps.exif_transpose(image)
        if image.width > max_side or image.height > max_side:
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        has_alpha = image.mode in ("RGBA", "P", "LA") and (
            image.mode != "P" or "transparency" in image.info)
        buffer = io.BytesIO()
        if has_alpha:
            image.save(buffer, "PNG", optimize=True)
            mime = "image/png"
        else:
            image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True)
            mime = "image/jpeg"
        return mime, buffer.getvalue(), ""
    except Exception as exc:  # битый файл/неподдержанный формат не роняет виджет
        log.warning("could not encode %s: %s", path, exc)
        return "", b"", f"Формат не поддерживается: {exc}"

def encode_photo(path: str, max_side: int, quality: int = 82) -> tuple[str, str]:

    mtime = ""
    try:
        mtime = str(os.stat(path).st_mtime_ns)
    except OSError:
        pass
    cache_key = (path, mtime, max_side, quality)
    hit = _cache.get(cache_key)
    if hit:
        _cache.move_to_end(cache_key)
        return hit[0], ""
    try:
        mime, payload, error = encode_bytes(path, max_side, quality)
    except OSError as exc:
        return "", str(exc)
    if not payload:
        return "", error
    result = f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")
    _cache_put(cache_key, result)
    return result, ""

def raw_data_url(path: str) -> tuple[str, str]:

    try:
        size = os.stat(path).st_size
    except OSError as exc:
        return "", f"Не удалось прочитать файл: {exc}"
    if size > MAX_INLINE_MEDIA:
        return "", (f"Медиа крупнее {MAX_INLINE_MEDIA // 1_000_000} МБ — локальный "
                    "стрим недоступен, а в мост кадр не помещается")
    ext = os.path.splitext(path)[1].lower()
    mime = ("video/mp4" if ext in (".mp4", ".mov", ".m4v") else
            "video/webm" if ext == ".webm" else "image/gif")
    raw = _read_source(path)
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii"), ""

def photo_payload(cfg: dict, raw_id: str, max_side: int, stage: str = "full") -> dict:

    path, error = resolve_photo(cfg, raw_id)
    if not path:
        return {"ok": False, "err": error}
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTS or stage == "raw":
        payload, error = raw_data_url(path)
        if not payload:
            return {"ok": False, "err": error}
        kind = "video" if ext in VIDEO_EXTS else "image"
        return {"ok": True, "data": payload, "kind": kind}
    if stage == "quick":
        max_side, quality = min(int(max_side or 0) or 720, 720), 68
    else:
        quality = 82
    payload, error = encode_photo(path, max_side, quality)
    if not payload:
        return {"ok": False, "err": error}
    return {"ok": True, "data": payload, "kind": "image"}

def caption_text(cfg: dict, photo: dict) -> str:
    mode = cfg["caption"]
    if mode == "name":
        return str(photo.get("name") or "")
    if mode == "date":
        stamp = photo.get("mtime") or 0
        return time.strftime("%d.%m.%Y", time.localtime(stamp)) if stamp else ""
    return ""
