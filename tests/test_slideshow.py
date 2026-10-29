import base64
import io

from PIL import Image as PILImage

from src import slideshow

def make_image(path, size=(12, 9), fmt=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = PILImage.new("RGB", size, "red")
    img.save(path, fmt)
    return path

def make_cfg(root, **over):
    cfg = dict(slideshow.CFG_DEFAULTS)
    cfg["folders"] = [str(p) for p in (root if isinstance(root, (list, tuple)) else [root])]
    cfg.update(over)
    return cfg

def test_defaults_and_clamps():
    cfg = slideshow.normalize_config({
        "folders": ["C:\\pics", "C:\\pics", "D:\\other"],
        "interval": "99999", "padding": "-4", "radius": 100,
        "transition": "левое-значение", "video_mode": "loop",
    })
    assert cfg["folders"] == ["C:\\pics", "D:\\other"]
    assert cfg["interval"] == 3600 and cfg["padding"] == 0 and cfg["radius"] == 48
    assert cfg["transition"] == "fade"
    assert cfg["video_mode"] == "loop"

def test_legacy_single_folder_key():
    cfg = slideshow.normalize_config({"folder": "C:\\pic"})
    assert cfg["folders"] == ["C:\\pic"]

def test_folders_split_by_separators():
    cfg = slideshow.normalize_config({"folders": "C:\\a ; D:\\b\nE:\\c"})
    assert cfg["folders"] == ["C:\\a", "D:\\b", "E:\\c"]

def test_transparent_kills_padding():
    cfg = slideshow.normalize_config({"frame": "transparent", "padding": 30})
    assert cfg["frame"] == "transparent" and cfg["padding"] == 0

def test_too_many_folders_capped():
    cfg = slideshow.normalize_config({"folders": [f"C:\\p{i}" for i in range(30)]})
    assert len(cfg["folders"]) == slideshow.MAX_FOLDERS

def test_photo_id_roundtrip_and_legacy():
    assert slideshow.photo_id(2, "sub/x.jpg") == "2::sub/x.jpg"
    assert slideshow.parse_photo_id("2::sub/x.jpg") == (2, "sub/x.jpg")
    assert slideshow.parse_photo_id("old.png") == (0, "old.png")
    assert slideshow.parse_photo_id("x::y") == (-1, "y")
    assert slideshow.parse_photo_id("5::y") == (5, "y")

def test_scan_flat_includes_images_and_skips_junk(tmp_path):
    root = tmp_path / "flat"
    make_image(root / "b.jpg")
    make_image(root / "a.png")
    (root / "c.gif").write_bytes(b"GIF87a")
    (root / "d.txt").write_text("no", encoding="utf-8")
    (root / ".hidden.jpg").write_text("no", encoding="utf-8")
    (root / "sub").mkdir()
    make_image(root / "sub" / "nested.png")
    cfg = make_cfg(root)
    photos, error = slideshow.scan_photos(cfg)
    assert error == ""
    assert [p["id"] for p in photos] == ["0::a.png", "0::b.jpg", "0::c.gif"]
    kinds = {p["id"]: p["kind"] for p in photos}
    assert kinds["0::c.gif"] == "image"

def test_scan_recursive_and_video_kinds(tmp_path):
    make_image(tmp_path / "x.jpg")
    (tmp_path / "sub").mkdir()
    make_image(tmp_path / "sub" / "y.webp")
    (tmp_path / "clip.mp4").write_bytes(b"0" * 64)
    (tmp_path / "clip.webm").write_bytes(b"0" * 64)
    (tmp_path / "nope.avi").write_bytes(b"0")
    cfg = make_cfg(tmp_path, recursive=True)
    photos, _ = slideshow.scan_photos(cfg)
    ids = [p["id"] for p in photos]
    assert ids == ["0::clip.mp4", "0::clip.webm", "0::sub/y.webp", "0::x.jpg"]
    kinds = {p["id"]: p["kind"] for p in photos}
    assert kinds["0::clip.mp4"] == "video" and kinds["0::clip.webm"] == "video"
    assert kinds["0::sub/y.webp"] == "image"

def test_scan_multiple_roots_prefixes(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    make_image(a / "one.jpg")
    make_image(b / "two.jpg")
    cfg = make_cfg([a, b])
    photos, error = slideshow.scan_photos(cfg)
    assert error == ""
    assert [p["id"] for p in photos] == ["0::one.jpg", "1::two.jpg"]

def test_scan_all_missing_is_error_partial_is_ok(tmp_path):
    ok = tmp_path / "ok"
    make_image(ok / "one.jpg")
    cfg = make_cfg([ok, tmp_path / "missing"])
    state = slideshow.state_for(cfg)
    assert state["ok"] and state["count"] == 1
    cfg = make_cfg([tmp_path / "missing"])
    state = slideshow.state_for(cfg)
    assert not state["ok"] and "не найдены" in state["err"]

def test_state_no_folders_error():
    cfg = slideshow.normalize_config({})
    state = slideshow.state_for(cfg)
    assert not state["ok"] and "папку" in state["err"]

def test_error_payloads_have_no_top_level_error_key():

    cfg = slideshow.normalize_config({})
    state = slideshow.state_for(cfg)
    assert "err" in state and "error" not in state
    payload = slideshow.photo_payload(cfg, "0::nope.jpg", 100)
    assert not payload.get("ok") and "err" in payload and "error" not in payload

def test_resolve_photo(tmp_path):
    make_image(tmp_path / "ok.jpg")
    cfg = make_cfg(tmp_path)
    path, error = slideshow.resolve_photo(cfg, "0::ok.jpg")
    assert error == "" and path.endswith("ok.jpg")
    path, error = slideshow.resolve_photo(cfg, "ok.jpg")  # legacy id без префикса
    assert error == ""
    _, error = slideshow.resolve_photo(cfg, "../other.jpg")
    assert error
    _, error = slideshow.resolve_photo(cfg, "1::ok.jpg")
    assert error
    _, error = slideshow.resolve_photo(cfg, "0::nope.jpg")
    assert error

def test_photo_payload_scales(tmp_path):
    make_image(tmp_path / "big.jpg", size=(900, 700))
    cfg = make_cfg(tmp_path)
    state = slideshow.state_for(cfg)
    pid = state["photos"][0]["id"]
    full = slideshow.photo_payload(cfg, pid, 300)
    assert full["ok"] and full["kind"] == "image"
    img = PILImage.open(io.BytesIO(base64.b64decode(full["data"].split(",", 1)[1])))
    assert max(img.size) <= 300

def test_photo_payload_video_inline(tmp_path):
    (tmp_path / "c.mp4").write_bytes(b"a" * 512)
    cfg = make_cfg(tmp_path)
    state = slideshow.state_for(cfg)
    payload = slideshow.photo_payload(cfg, state["photos"][0]["id"], 0, stage="raw")
    assert payload["ok"] and payload["kind"] == "video"
    assert payload["data"].startswith("data:video/mp4;base64,")

def test_raw_data_url_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(slideshow, "MAX_INLINE_MEDIA", 16)
    (tmp_path / "c.mp4").write_bytes(b"a" * 64)
    data, error = slideshow.raw_data_url(str(tmp_path / "c.mp4"))
    assert data == "" and "Медиа крупнее" in error

def test_gif_payload_keeps_animation(tmp_path):
    src = tmp_path / "g.gif"
    frame = PILImage.new("P", (4, 4))
    frame.save(src, "GIF")
    mime, payload, error = slideshow.encode_bytes(str(src), 2)
    assert error == "" and mime == "image/gif" and payload

def test_media_token_stable_and_differs():
    a = slideshow.normalize_config({"folders": ["C:\\a"]})
    b = slideshow.normalize_config({"folders": ["C:\\a"]})
    c = slideshow.normalize_config({"folders": ["C:\\b"]})
    assert slideshow.media_token(a) == slideshow.media_token(b)
    assert slideshow.media_token(a) != slideshow.media_token(c)

def test_media_urls_stages(tmp_path):
    make_image(tmp_path / "p.jpg")
    make_image(tmp_path / "g.gif", fmt="GIF")
    (tmp_path / "v.mp4").write_bytes(b"z" * 8)
    cfg = make_cfg(tmp_path)
    state = slideshow.state_for(cfg, media_base="http://127.0.0.1:9/m/tok/")
    by = {p["id"]: p for p in state["photos"]}
    assert by["0::p.jpg"]["url"].startswith("http://127.0.0.1:9/m/tok/")
    assert "/full/0/p.jpg?v=" in by["0::p.jpg"]["url"]
    assert "/raw/0/g.gif" in by["0::g.gif"]["url"]     # анимация — без пережатия
    assert "/raw/0/v.mp4?v=" in by["0::v.mp4"]["url"]
    state = slideshow.state_for(cfg)
    assert all(p["url"] == "" for p in state["photos"])  # без base — фолбэки

def test_media_urls_escape_spaces(tmp_path):
    make_image(tmp_path / "my photo.jpg")
    cfg = make_cfg(tmp_path)
    state = slideshow.state_for(cfg, media_base="http://x/m/t/")
    url = state["photos"][0]["url"]
    assert "my%20photo.jpg" in url

def test_caption_text_modes():
    photo = {"name": "Вид с прогулки", "mtime": 0}
    cfg = slideshow.normalize_config({"caption": "name"})
    assert slideshow.caption_text(cfg, photo) == "Вид с прогулки"
    cfg = slideshow.normalize_config({"caption": "date"})
    assert slideshow.caption_text(cfg, photo) == ""
    photo["mtime"] = 1_700_000_000
    assert slideshow.caption_text(cfg, photo).count(".") == 2
