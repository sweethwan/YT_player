"""Application entry point and portable data directory setup."""

import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .core import SettingsStore, Task, TaskStore, app_root, atomic_json
from .processes import Tools
from .ui import STYLE, MainWindow
from .updates import recover_update


def main() -> int:
    parser = argparse.ArgumentParser(description="YT Player")
    parser.add_argument(
        "--smoke-test", metavar="LOCAL_VIDEO", help="run packaged playback diagnostics"
    )
    args, qt_args = parser.parse_known_args()
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("YT Player")
    app.setOrganizationName("YTDesktopPlayer")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    root = app_root()
    runtime_root = root / ".tmp" / "smoke-test" if args.smoke_test else root
    try:
        data = runtime_root / "data"
        data.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(data / "app.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            QMessageBox.information(None, "YT Player", "이 폴더의 앱이 이미 실행 중입니다.")
            return 0
        probe = data / ".write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        handler = RotatingFileHandler(
            data / "app.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8"
        )
        logging.basicConfig(
            level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s"
        )
        tools = Tools(root)
        recover_update(tools)
        settings = SettingsStore(runtime_root)
        if args.smoke_test:
            settings.value.check_updates = False
            settings.value.muted = True
        store = TaskStore(runtime_root)
    except OSError as exc:
        QMessageBox.critical(
            None, "앱 폴더 접근 오류", f"앱 폴더를 쓰기 가능한 위치로 이동하세요.\n{exc}"
        )
        return 1
    window = MainWindow(tools, settings, store)
    window.show()

    def report_exception(kind, value, traceback):
        logging.error("Unhandled exception", exc_info=(kind, value, traceback))
        window.notice(f"오류가 발생했습니다: {value} · data/app.log를 확인하세요.")

    sys.excepthook = report_exception
    if args.smoke_test:
        run_smoke_test(app, window, Path(args.smoke_test).resolve())
    result = app.exec()
    lock.unlock()
    return result


def run_smoke_test(app: QApplication, window: MainWindow, video: Path) -> None:
    """Exercise the shipped Qt/mpv stack without a development Python installation."""
    done = [False]
    checks = {"click_play_pause": False, "fullscreen_input": False, "thumbnail": False}
    phase = ["waiting"]
    deadline = [0.0]

    def finish(code, error=""):
        if done[0]:
            return
        if window.updater.worker is not None:
            QTimer.singleShot(100, lambda: finish(code, error))
            return
        done[0] = True
        atomic_json(
            window.store.path.parent / "smoke-result.json",
            {
                "exit_code": code,
                "error": error,
                "frozen": bool(getattr(sys, "frozen", False)),
                "python": sys.version,
                "executable": sys.executable,
                "qt_libraries": QLibraryInfo.path(QLibraryInfo.LibraryPath.LibrariesPath),
                "missing_tools": window.tools.missing(),
                "playback_position": window.player.current_position,
                "interaction_checks": checks,
            },
        )
        window.updater.poll.stop()
        window.controller.shutdown()
        window.settings.flush()
        app.exit(code)

    def check_input():
        if done[0]:
            return
        if time.monotonic() > deadline[0]:
            finish(1, "native playback input timeout")
        elif phase[0] == "pause" and window.player.paused:
            phase[0] = "resume"
            window.player.command(["keypress", "MBTN_LEFT"])
            QTimer.singleShot(100, check_input)
        elif phase[0] == "resume" and not window.player.paused:
            checks["click_play_pause"] = True
            phase[0] = "enter"
            window.player.command(["keypress", "MBTN_LEFT"])
            window.player.command(["keypress", "MBTN_LEFT_DBL"])
            QTimer.singleShot(100, check_input)
        elif phase[0] == "enter" and window.fullscreen:
            phase[0] = "exit"
            window.player.command(["keypress", "ESC"])
            QTimer.singleShot(100, check_input)
        elif phase[0] == "exit" and not window.fullscreen:
            checks["fullscreen_input"] = True
            phase[0] = "preview"
            window.controller.previews.request(
                min(1.25, window.player.duration / 2), window.player.duration
            )
        else:
            QTimer.singleShot(100, check_input)

    def playback_started(_task):
        phase[0] = "pause"
        deadline[0] = time.monotonic() + 10
        window.player.command(["keypress", "MBTN_LEFT"])
        QTimer.singleShot(100, check_input)

    def preview_ready(_serial, _seconds, image, error):
        checks["thumbnail"] = image is not None and not image.isNull()
        if not checks["thumbnail"]:
            finish(1, error)
            return
        # These records exist only in the isolated smoke-test data directory.
        for index in range(23):
            recent = Task.create("local", str(index), "1080", window.store.root)
            recent.title = f"사이드바 검증 영상 {index + 1} · 긴 제목 표시 확인"
            recent.state = "cancelled"
            recent.list_order = index + 1
            window.store.tasks[recent.id] = recent
        window.store.show_recent(task)
        window.select_task(task.id)
        checks["sidebar_recent"] = window.task_list.count() == 20 and window.list_ids[0] == task.id
        window.grab().save(str(window.store.path.parent / "sidebar.png"))
        window.resize(800, 600)
        QTimer.singleShot(100, check_sidebar)

    def check_sidebar():
        window.grab().save(str(window.store.path.parent / "sidebar-small.png"))
        playing = window.player.process
        window.clear_button.click()
        checks["sidebar_clear"] = (
            window.task_list.count() == 0
            and window.player.process is playing
            and window.player.ready
            and not window.cancel.isEnabled()
            and not window.retry.isEnabled()
            and not window.stop.icon().isNull()
            and window.width() == 800
        )
        finish(
            0 if all(checks.values()) else 1, "" if all(checks.values()) else "sidebar check failed"
        )

    window.player.started.connect(playback_started)
    window.controller.previews.ready.connect(preview_ready)
    window.player.failed.connect(lambda _task, error: finish(1, error))
    QTimer.singleShot(60000, lambda: finish(1, "playback timeout"))
    task = Task.create("local", "smoke-test", "1080", window.tools.root / "data")
    window.store.tasks[task.id] = task
    window.controller.current_id = task.id
    QTimer.singleShot(200, lambda: window.controller._play(task, str(video)))


if __name__ == "__main__":
    raise SystemExit(main())
