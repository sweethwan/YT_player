"""External tool paths, process environment and Windows child-process ownership."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment


class Tools:
    def __init__(self, root: Path):
        self.root = root
        self.bin = root / "tools"

    def path(self, name: str) -> str:
        return str(self.bin / f"{name}.exe")

    def missing(self) -> list[str]:
        return [
            name
            for name in ["yt-dlp", "mpv", "ffmpeg", "ffprobe", "deno"]
            if not Path(self.path(name)).is_file()
        ]

    def environment(self) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PATH", str(self.bin) + os.pathsep + env.value("PATH"))
        cache = self.root / "data" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        env.insert("DENO_DIR", str(cache / "deno"))
        env.insert("TEMP", str(cache))
        env.insert("TMP", str(cache))
        env.insert("PYTHONIOENCODING", "utf-8")
        return env

    def ytdlp_args(self) -> list[str]:
        return [
            "--ignore-config",
            "--no-plugin-dirs",
            "--no-playlist",
            "--encoding",
            "utf-8",
            "--no-cache-dir",
            "--socket-timeout",
            "20",
            "--retries",
            "3",
            "--ffmpeg-location",
            str(self.bin),
            "--js-runtimes",
            f"deno:{self.path('deno')}",
        ]


class ProcessTree:
    """A kill-on-close Job Object owns yt-dlp's ffmpeg/Deno descendants on Windows."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in [
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                ]
            ]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            self.close()
            raise OSError("Windows 프로세스 트리 보호를 설정할 수 없습니다.")

    def assign(self, pid: int) -> None:
        if not self.handle:
            return
        process = self.kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            raise OSError("자식 프로세스 핸들을 열 수 없습니다.")
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise OSError("자식 프로세스를 Windows 작업에 연결할 수 없습니다.")
        finally:
            self.kernel.CloseHandle(process)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class OwnedProcess(QProcess):
    def __init__(self, tools: Tools, parent=None):
        super().__init__(parent)
        self.tree = ProcessTree()
        self.setProcessEnvironment(tools.environment())
        self.started.connect(self._own)
        self.finished.connect(lambda *_: self.tree.close())

    def _own(self):
        try:
            self.tree.assign(self.processId())
        except OSError:
            self.kill()

    def stop(self):
        self.tree.close()
        if self.state() != QProcess.ProcessState.NotRunning:
            self.kill()

    def shutdown(self):
        self.stop()
        self.waitForFinished(2500)
