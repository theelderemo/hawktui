from __future__ import annotations

from pathlib import Path

import pytest

import hawktui.hawktui as ht
from hawktui.hawktui import DEFAULTS, build_command, extract_urls, find_ytdlp

URL = "https://example.com/v"


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def _cfg(**overrides) -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(overrides)
    return cfg


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(ht, "YTDLP_MANAGED", tmp_path / ".local/share/hawktui/yt-dlp")
    monkeypatch.setattr(ht.shutil, "which", lambda cmd: None)
    return tmp_path


def test_explicit_path_beats_managed(fake_home):
    _exe(ht.YTDLP_MANAGED)
    assert find_ytdlp(_cfg(ytdlp_path="/x/custom")) == "/x/custom"


def test_managed_beats_local_bin(fake_home):
    _exe(ht.YTDLP_MANAGED)
    _exe(fake_home / ".local/bin/yt-dlp")
    assert find_ytdlp(_cfg()) == str(ht.YTDLP_MANAGED)


def test_local_bin_beats_which(fake_home, monkeypatch):
    local = _exe(fake_home / ".local/bin/yt-dlp")
    monkeypatch.setattr(ht.shutil, "which", lambda cmd: "/usr/bin/yt-dlp")
    assert find_ytdlp(_cfg()) == str(local)


def test_which_beats_bare_fallback(fake_home, monkeypatch):
    monkeypatch.setattr(ht.shutil, "which", lambda cmd: "/usr/bin/yt-dlp")
    assert find_ytdlp(_cfg()) == "/usr/bin/yt-dlp"


def test_bare_fallback(fake_home):
    assert find_ytdlp(_cfg()) == "yt-dlp"


def test_build_command_uses_given_binary():
    cmd = build_command(dict(DEFAULTS), URL, ytdlp="/x/yt-dlp")
    assert cmd[0] == "/x/yt-dlp"
    assert cmd[-2:] == ["--", URL]


def test_build_command_extract_audio():
    cmd = build_command(_cfg(extract_audio=True), URL, ytdlp="/x/yt-dlp")
    assert "-x" in cmd


def test_build_command_no_playlist():
    cmd = build_command(_cfg(no_playlist=True), URL, ytdlp="/x/yt-dlp")
    assert "--no-playlist" in cmd


def test_build_command_limit_rate():
    cmd = build_command(_cfg(limit_rate="2M"), URL, ytdlp="/x/yt-dlp")
    i = cmd.index("--limit-rate")
    assert cmd[i:i + 2] == ["--limit-rate", "2M"]


def test_extract_urls():
    assert extract_urls("see https://a.io/x, and (https://b.io/y).") == [
        "https://a.io/x",
        "https://b.io/y",
    ]
