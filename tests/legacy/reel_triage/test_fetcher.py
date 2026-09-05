from reel_triage import config, fetcher


def test_ytdlp_command_has_no_cookies():
    cmd = fetcher.build_ytdlp_cmd("https://instagram.com/reel/A", "/tmp/A.mp4")
    joined = " ".join(cmd)
    assert "--cookies" not in joined
    assert "--cookies-from-browser" not in joined
    assert "https://instagram.com/reel/A" in cmd
    assert "/tmp/A.mp4" in cmd


def test_keyframe_cmd_caps_at_max():
    cmd = fetcher.build_keyframe_cmd("/tmp/A.mp4", "/tmp/frames")
    assert str(config.MAX_KEYFRAMES) in cmd
    assert "/tmp/A.mp4" in cmd


def test_cleanup_media_removes_file(tmp_path):
    f = tmp_path / "reel.mp4"
    f.write_bytes(b"x")
    assert f.exists()
    fetcher.cleanup_media(str(f))
    assert not f.exists()


def test_cleanup_media_noop_when_absent(tmp_path):
    # must not raise on a missing file
    fetcher.cleanup_media(str(tmp_path / "missing.mp4"))
