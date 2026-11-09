from scripts.promote_build import promote


def test_promote_keeps_user_data_and_never_publishes_diagnostics(tmp_path):
    stage, destination = tmp_path / "stage", tmp_path / "destination"
    stage.mkdir()
    destination.mkdir()
    (stage / "YTPlayer.exe").write_bytes(b"new")
    (destination / "YTPlayer.exe").write_bytes(b"old")
    for name in ["data", "downloads"]:
        (destination / name).mkdir()
        (destination / name / "keep.txt").write_text("user-owned")
        (stage / name).mkdir()
        (stage / name / "keep.txt").write_text("test data")
    (stage / ".tmp").mkdir()
    (stage / ".tmp" / "smoke.txt").write_text("diagnostic")
    promote(stage, destination)
    assert (destination / "YTPlayer.exe").read_bytes() == b"new"
    assert (destination / "data" / "keep.txt").read_text() == "user-owned"
    assert (destination / "downloads" / "keep.txt").read_text() == "user-owned"
    assert not (destination / ".tmp").exists()
