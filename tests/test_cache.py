from src import slideshow

def make_cfg(tmp_path, **over):
    cfg = dict(slideshow.CFG_DEFAULTS)
    cfg["folders"] = [str(tmp_path)]
    cfg.update(over)
    return cfg

def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 32)
    return path

def test_scan_ttl_cache_and_force(tmp_path):
    cfg = make_cfg(tmp_path)
    touch(tmp_path / "one.jpg")
    first = slideshow.state_for(cfg)
    assert first["count"] == 1
    touch(tmp_path / "two.jpg")
    cached = slideshow.state_for(cfg)  # TTL-кеш: новый файл ещё не виден
    assert cached["count"] == 1
    forced = slideshow.state_for(cfg, force=True)
    assert forced["count"] == 2

def test_quick_stage_is_small(tmp_path):
    cfg = make_cfg(tmp_path)
    image_path = touch(tmp_path / "big.jpg")
    from PIL import Image as PILImage
    PILImage.new("RGB", (2000, 1400), "green").save(image_path)
    quick = slideshow.photo_payload(cfg, "0::big.jpg", 2000, stage="quick")
    assert quick["ok"]
    import base64
    import io
    from PIL import Image as PILImage
    again = PILImage.open(io.BytesIO(base64.b64decode(quick["data"].split(",", 1)[1])))
    assert max(again.size) <= 720
