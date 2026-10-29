from __future__ import annotations

import ctypes
import logging
import sys
import threading

log = logging.getLogger("photo-frame.picker")

PICK_RESULT: dict = {"status": "idle", "path": "", "error": ""}
_PICK_LOCK = threading.Lock()
MAX_PATH = 260

BIF_RETURNONLYFSDIRS = 0x0001
BIF_EDITBOX = 0x0010
BIF_NEWDIALOGSTYLE = 0x0040   # разворачиваемое дерево с Computer/сеть
BIF_NONEWFOLDERBUTTON = 0x0200

BFFM_INITIALIZED = 1
BFFM_SETSTATUSTEXTW = 0x0464   # WM_USER+100
BFFM_SETEXTENT = 0x049F        # WM_USER+159 (только новый стиль диалога)
_BROWSE_CB = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
                               ctypes.c_long, ctypes.c_long)
_CALLBACK_HOLDER: list = []  # не даём ctypes-колбэку быть собран garbage-ом
_BFF_TEXT = ""
_BFF_PREFILL = ""

class BROWSEINFOW(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", ctypes.c_void_p),
        ("pidlRoot", ctypes.c_void_p),
        ("szDisplayName", ctypes.c_wchar * MAX_PATH),  # статический буфер, не c_wchar_p
        ("lpszTitle", ctypes.c_wchar_p),
        ("ulFlags", ctypes.c_uint),
        ("lpfn", ctypes.c_void_p),
        ("lParam", ctypes.c_longlong),  # LPARAM: размер указателя
        ("pVoid", ctypes.c_void_p),     # pointer (Vista+)
        ("iIcon", ctypes.c_int),
    ]

def _send_text(hwnd: int, msg: int, text: str) -> None:
    buffer = ctypes.create_unicode_buffer(text)
    user32 = ctypes.windll.user32
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_long,
                                    ctypes.c_void_p]
    user32.SendMessageW.restype = ctypes.c_long
    user32.SendMessageW(hwnd, msg, 0, ctypes.byref(buffer))

def _browse_callback(hwnd, msg, _lparam, _lpdata):
    if msg == 1:  # BFFM_INITIALIZED
        if _BFF_TEXT:
            _send_text(hwnd, BFFM_SETSTATUSTEXTW, _BFF_TEXT)
        if _BFF_PREFILL:
            _send_text(hwnd, BFFM_SETEXTENT, _BFF_PREFILL)
    return 0

def _pick_windows(prefill: str = "") -> tuple[str, str]:
    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    user32 = ctypes.windll.user32
    try:
        global _BFF_TEXT, _BFF_PREFILL
        _BFF_TEXT = "Снимки и видео для рамки выбираются здесь"
        import os as _os
        _BFF_PREFILL = prefill if prefill and _os.path.isdir(prefill) else ""
        cb = _BROWSE_CB(_browse_callback)
        _CALLBACK_HOLDER.clear()
        _CALLBACK_HOLDER.append(cb)

        shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
        shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
        shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        shell32.SHGetPathFromIDListW.restype = ctypes.c_bool
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

        info = BROWSEINFOW()
        foreground = user32.GetForegroundWindow()
        info.hwndOwner = foreground or None  # поверх окна настроек
        info.pidlRoot = None
        info.lpszTitle = "Выберите папку с фото и видео для рамки"
        info.ulFlags = (BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX
                        | BIF_NONEWFOLDERBUTTON)
        info.lpfn = ctypes.cast(cb, ctypes.c_void_p)
        info.lParam = 0
        info.pVoid = None
        info.iIcon = 0

        ole32.CoInitializeEx(None, 0x2)  # STA этого фонового потока
        try:
            pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
            if not pidl:
                return "", ""  # отмена
            buffer = ctypes.create_unicode_buffer(1024)
            ok = shell32.SHGetPathFromIDListW(pidl, buffer)
            ole32.CoTaskMemFree(pidl)
            if not ok:
                return "", "Диалог вернул некорректный путь"
            return buffer.value or "", ""
        finally:
            ole32.CoUninitialize()
    except Exception as exc:
        log.warning("folder picker failed: %s", exc)
        return "", f"Не удалось открыть диалог: {exc}"

def pick_folder(params=None) -> dict:

    with _PICK_LOCK:
        if PICK_RESULT["status"] == "picking":
            return {"ok": True, "status": "picking"}
        PICK_RESULT.update(status="picking", path="", error="")

    def worker():
        prefill = ""
        if isinstance(params, dict):
            raw = params.get("prefill")
            if isinstance(raw, str):
                prefill = raw
        if sys.platform == "win32":
            path, error = _pick_windows(prefill)
        else:
            path, error = "", "Выбор папки кликом доступен в Windows — путь можно ввести вручную"
        with _PICK_LOCK:
            PICK_RESULT.update(status="done", path=path, error=error)

    threading.Thread(target=worker, name="photo-frame-picker", daemon=True).start()
    return {"ok": True, "status": "started"}

def pick_result(params=None) -> dict:
    with _PICK_LOCK:
        result = dict(PICK_RESULT)
        if result["status"] == "done":
            PICK_RESULT.update(status="idle", path="", error="")
    return {"ok": True, "status": result["status"], "path": result["path"],
            "err": result["error"]}
