import io
import json
import urllib.error
import urllib.request

from PIL import Image as PILImage

from src import slideshow
from src.media_server import MediaServer

def fetch(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers.get_content_type(), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, "http-error", exc.read()

def make_cfg(root):
    cfg = dict(slideshow.CFG_DEFAULTS)
    cfg["folders"] = [str(root)]
    return cfg

def test_media_server_serves_stages(tmp_path):
    (tmp_path / "p.jpg").parent  # noqa
    from PIL import Image as PILImage
    PILImage.new("RGB", (300, 200), "blue").save(tmp_path / "p.jpg")
    (tmp_path / "g.gif").write_bytes(b"GIF87a-still")
    (tmp_path / "v.mp4").write_bytes(b"M" * 5000)
    cfg = make_cfg(tmp_path)

    server = MediaServer()
    base = server.start()
    assert base, "сервер не поднялся"
    token = slideshow.media_token(cfg)
    server.remember(token, cfg["folders"])
    media_base = base + token + "/"
    state = slideshow.state_for(cfg, media_base=media_base)

    by = {p["id"]: p for p in state["photos"]}

    code, mime, body = fetch(by["0::p.jpg"]["url"] + "&s=150")
    assert code == 200 and mime == "image/jpeg"
    img = PILImage.open(io.BytesIO(body))
    assert max(img.size) <= 150

    quick_url = by["0::p.jpg"]["url"].replace("/full/", "/quick/")
    code, mime, body = fetch(quick_url)
    assert code == 200 and mime == "image/jpeg"
    assert max(PILImage.open(io.BytesIO(body)).size) <= 720

    code, mime, body = fetch(by["0::g.gif"]["url"])
    assert code == 200 and mime == "image/gif" and body == b"GIF87a-still"

    code, mime, body = fetch(by["0::v.mp4"]["url"])
    assert code == 200 and mime == "video/mp4" and body == b"M" * 5000

    code, mime, body = fetch(by["0::v.mp4"]["url"], {"Range": "bytes=10-19"})
    assert code == 206 and len(body) == 10

    code, _, _ = fetch(base + token + "/full/0/../escaped.jpg")
    assert code == 404
    code, _, _ = fetch(base + "0123456789abcdef" + "/full/0/p.jpg")
    assert code == 404

    code, _, _ = fetch(base + token + "/full/9/p.jpg")
    assert code == 404
