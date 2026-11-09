"""Explicit opt-in tests against actual Windows executables, without YouTube access."""

import ctypes
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from yt_player.core import Task
from yt_player.player import Player
from yt_player.processes import OwnedProcess, Tools

pytestmark = [pytest.mark.runtime, pytest.mark.skipif(os.name != "nt", reason="Windows only")]
ROOT = Path(__file__).resolve().parent.parent


def test_process_tree_stops_descendants(qtbot, tmp_path):
    process = OwnedProcess(Tools(tmp_path))
    process.start(
        sys.executable,
        [
            "-u",
            "-c",
            "import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(p.pid,flush=True); time.sleep(60)",
        ],
    )
    try:
        qtbot.waitUntil(lambda: process.bytesAvailable() > 0, timeout=10000)
        pid = int(bytes(process.readAllStandardOutput()).strip())
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        assert handle
        try:
            assert kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT / alive
            process.shutdown()
            qtbot.waitUntil(lambda: kernel.WaitForSingleObject(handle, 0) == 0, timeout=3000)
        finally:
            kernel.CloseHandle(handle)
    finally:
        process.shutdown()


def test_embedded_mpv_ipc_local_playback_and_controls(qtbot, tmp_path):
    tools = Tools(tmp_path)
    tools.bin = ROOT / "tools"
    if tools.missing():
        pytest.skip("Run scripts/bootstrap_tools.py first")
    sample = tmp_path / "한글 sample.mkv"
    subprocess.run(
        [
            tools.path("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "8",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            str(sample),
        ],
        check=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    video = QWidget()
    video.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    video.resize(640, 360)
    qtbot.addWidget(video)
    video.show()
    player = Player(tools, int(video.winId()))
    player.muted = True
    task = Task.create("url", "video", "1080", tmp_path)
    replies, errors = [], []
    player.ipc_reply.connect(replies.append)
    player.failed.connect(lambda *args: errors.append(args))
    try:
        player.play(task, str(sample))
        qtbot.waitUntil(lambda: player.ready or bool(errors), timeout=20000)
        assert not errors, errors
        assert player.ready
        assert all(reply.get("error") == "success" for reply in replies), replies
        player.set_volume(15)
        player.set_muted(True)
        player.set_speed(1.5)
        player.seek(3)
        qtbot.waitUntil(lambda: player.current_position >= 3, timeout=5000)
        player.toggle_pause()
        qtbot.wait(100)
        assert player.duration > 7
    finally:
        player.stop()
        video.close()
