from __future__ import annotations

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from . import slideshow

log = logging.getLogger("photo-frame.media")

MAX_AGE = 60 * 60 * 24 * 365  # ?v=mtime сам инвалидирует кеш
STAGES = ("raw", "quick", "full")

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # тишина: шум виджета не нужен
        pass

    def _send(self, code: int, body: bytes, mime: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", f"public, max-age={MAX_AGE}, immutable")
        self.send_header("Accept-Ranges", "bytes")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        media = getattr(self.server, "media", None)
        parts = [unquote(p) for p in urlsplit(self.path).path.strip("/").split("/")]
        if media is None or len(parts) < 5 or parts[0] != "m":
            self._send(404, b"not found", "text/plain")
            return
        token, stage, idx = parts[1], parts[2], parts[3]
        rel_parts = parts[4:]
        if stage not in STAGES:
            self._send(404, b"bad stage", "text/plain")
            return
        roots = media.roots_for(token)
        try:
            folder_index = int(idx)
        except ValueError:
            self._send(404, b"bad folder index", "text/plain")
            return
        if folder_index < 0 or folder_index >= len(roots) or not rel_parts:
            self._send(404, b"bad folder index", "text/plain")
            return
        root = os.path.realpath(roots[folder_index])
        candidate = os.path.realpath(os.path.join(root, "/".join(rel_parts)))
        if not candidate.startswith(root + os.sep) or not os.path.isfile(candidate):
            self._send(404, b"not a file", "text/plain")
            return
        if stage == "raw":
            self._serve_file(candidate)
            return
        side = media.query_side(self.path, stage)
        try:
            mime, body, error = slideshow.encode_bytes(candidate, side,
                                                      68 if stage == "quick" else 82)
        except OSError as exc:
            self._send(500, str(exc).encode("utf-8", "replace"), "text/plain")
            return
        if not body:
            self._send(500, error.encode("utf-8", "replace"), "text/plain")
            return
        self._send(200, body, mime)

    def _serve_file(self, path: str):
        ext = os.path.splitext(path)[1].lower()
        mime = slideshow.MIME_BY_EXT.get(ext, "application/octet-stream")
        try:
            size = os.path.getsize(path)
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                spec = rng[6:].split(",")[0].split("-")
                start = int(spec[0] or 0)
                end = int(spec[1]) if len(spec) > 1 and spec[1] else size - 1
                end = min(end, size - 1)
                if start > end:
                    self._send(416, b"", mime)
                    return
                with open(path, "rb") as handle:
                    handle.seek(start)
                    body = handle.read(end - start + 1)
                self._send(206, body, mime,
                           {"Content-Range": f"bytes {start}-{end}/{size}"})
                return
            with open(path, "rb") as handle:
                body = handle.read()
            self._send(200, body, mime)
        except OSError:
            self._send(500, b"read error", "text/plain")

class MediaServer:

    def __init__(self):
        self._httpd: ThreadingHTTPServer | None = None
        self._roots_by_token: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self.base = ""

    def start(self) -> str:
        with self._lock:
            if self._httpd:
                return self.base
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            except OSError as exc:
                log.warning("media server disabled: %s", exc)
                return ""
            httpd.media = self
            threading.Thread(target=httpd.serve_forever,
                             name="photo-frame-media", daemon=True).start()
            self._httpd = httpd
            self.base = f"http://127.0.0.1:{httpd.server_address[1]}/m/"
            return self.base

    def remember(self, token: str, roots: list[str]) -> None:
        with self._lock:
            self._roots_by_token[token] = list(roots)
            while len(self._roots_by_token) > 24:
                self._roots_by_token.pop(next(iter(self._roots_by_token)))

    def roots_for(self, token: str) -> list[str]:
        with self._lock:
            return list(self._roots_by_token.get(token, []))

    def query_side(self, path: str, stage: str) -> int:
        query = parse_qs(urlsplit(path).query)
        if stage == "quick":
            return 720
        try:
            return max(64, min(2400, int(query.get("s", ["1600"])[0])))
        except ValueError:
            return 1600
