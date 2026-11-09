"""Opt-in storyboard and remote-frame preview test using one public YouTube URL."""

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from yt_player.core import SettingsStore, Task, TaskStore, normalize_url
from yt_player.downloads import MetadataProbe
from yt_player.previews import PreviewService
from yt_player.processes import Tools


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Pass a public YouTube video URL")
    project = Path(__file__).resolve().parent.parent
    root = project / ".tmp" / f"preview-smoke-{int(time.time())}"
    root.mkdir(parents=True)
    app = QApplication([])
    settings, store = SettingsStore(root), TaskStore(root)
    tools = Tools(root)
    tools.bin = project / "tools"
    url, video_id = normalize_url(sys.argv[1])
    task = Task.create(url, video_id, "1080", root / "downloads")
    service = PreviewService(tools, settings, store)
    probe = MetadataProbe(tools, task)
    result = {"fast": False, "precise": False}
    duration = [0]
    done = [False]

    def finish(code, error=""):
        if done[0]:
            return
        done[0] = True
        result.update(exit_code=code, error=error)
        print(json.dumps(result), flush=True)
        (root / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        probe.stop()
        service.cancel()
        app.exit(code)

    def metadata(task_id, info):
        duration[0] = info["preview"]["duration"]
        result["storyboard_available"] = bool(info["preview"]["storyboard"])
        print("Metadata ready", flush=True)
        service.remember(task_id, info)
        service.set_source(task)
        service.request(min(5.3, duration[0] / 3), duration[0])

    def ready(_serial, seconds, image, error):
        mode = settings.value.thumbnail_mode
        if image is None:
            finish(1, f"{mode}: {error}")
            return
        result[mode] = True
        image.save(str(root / f"{mode}.png"))
        print(f"{mode} preview at {seconds:.3f}s: {image.width()}x{image.height()}", flush=True)
        if mode == "fast":
            result["sheet_cached"] = any(key[0] == "sheet" for key in service.cache.images)
            service.cancel()
            settings.value.thumbnail_mode = "precise"
            service.request(min(10.75, duration[0] * 2 / 3), duration[0])
        else:
            finish(0)

    probe.ready.connect(metadata)
    probe.failed.connect(lambda _id, error: finish(1, error))
    service.ready.connect(ready)
    QTimer.singleShot(120000, lambda: finish(2, "timeout"))
    QTimer.singleShot(0, probe.start)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
