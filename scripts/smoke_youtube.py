"""Opt-in real YouTube smoke test, isolated from the user's settings/downloads.

uv run python scripts/smoke_youtube.py https://www.youtube.com/watch?v=VIDEO_ID
"""

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from yt_player.core import SettingsStore, TaskStore
from yt_player.processes import Tools
from yt_player.ui import STYLE, MainWindow


class ThrottledTools(Tools):
    def ytdlp_args(self):
        return super().ytdlp_args() + ["--limit-rate", "64K"]


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Pass one public YouTube video URL")
    project = Path(__file__).resolve().parent.parent
    root = project / ".tmp" / f"youtube-smoke-{int(time.time())}"
    root.mkdir(parents=True)
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    settings, store = SettingsStore(root), TaskStore(root)
    settings.value.check_updates = False
    settings.value.muted = True
    tools = ThrottledTools(root)
    tools.bin = project / "tools"
    window = MainWindow(tools, settings, store)
    window.show()
    start = time.monotonic()
    result = {"started_at": None, "completed_at": None, "started_before_complete": False}
    states = {}

    def changed(task_id):
        task = store.tasks[task_id]
        if states.get(task_id) != task.state:
            states[task_id] = task.state
            print(f"{time.monotonic() - start:.1f}s download={task.state} {task.error}", flush=True)
        if task.state == "failed":
            finish(1)
        elif task.state == "completed":
            result["completed_at"] = round(time.monotonic() - start, 2)
            if result["started_at"] is not None:
                QTimer.singleShot(200, lambda: finish(0))

    def started(task_id):
        result["started_at"] = round(time.monotonic() - start, 2)
        result["started_before_complete"] = store.tasks[task_id].state != "completed"
        print(f"{result['started_at']}s playback started", flush=True)
        window.grab().save(str(root / "playing.png"))
        if result["completed_at"] is not None:
            QTimer.singleShot(200, lambda: finish(0))

    def finish(code):
        result["exit_code"] = code
        (root / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)
        window.close()
        app.exit(code)

    window.controller.changed.connect(changed)
    window.player.started.connect(started)
    window.controller.message.connect(lambda msg: print(msg, flush=True))
    QTimer.singleShot(180000, lambda: finish(2))
    QTimer.singleShot(300, lambda: window.controller.open(sys.argv[1]))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
