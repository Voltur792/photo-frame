from src import folder_picker

def test_pick_result_shape_without_error_key():
    r = folder_picker.pick_result()
    assert r["ok"] and r["status"] == "idle"
    assert "err" in r and "error" not in r
    assert r["path"] == ""

def test_pick_folder_started_shape(monkeypatch):
    monkeypatch.setattr(folder_picker, "_pick_windows",
                        lambda prefill="": ("D:\\pics", ""))
    r = folder_picker.pick_folder({})
    assert r["ok"] and r["status"] == "started"
    assert "error" not in r
    import time
    deadline = time.time() + 5
    done = None
    while time.time() < deadline:
        r2 = folder_picker.pick_result()
        if r2["status"] == "done":
            done = r2
            break
        time.sleep(0.05)
    assert done is not None
    assert done["path"] == "D:\\pics"
    assert "err" in done and "error" not in done
