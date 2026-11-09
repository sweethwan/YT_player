from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage

import yt_player.previews as preview_module
from yt_player.core import SettingsStore, Task, TaskStore
from yt_player.preview_data import preview_metadata, storyboard_cell
from yt_player.previews import ImageCache, PreviewService
from yt_player.processes import Tools
from yt_player.widgets import Timeline


def make_service(tmp_path):
    settings, store = SettingsStore(tmp_path), TaskStore(tmp_path)
    service = PreviewService(Tools(tmp_path), settings, store)
    task = Task.create("url", "video", "1080", tmp_path)
    service.set_source(task, "local.mkv")
    return service, settings, task


def test_storyboard_sheet_boundaries_and_end():
    board = {
        "width": 160,
        "height": 90,
        "columns": 2,
        "rows": 2,
        "fps": 0.5,
        "fragments": [{"url": "https://example.com/0.jpg"}, {"url": "https://example.com/1.jpg"}],
    }
    assert storyboard_cell(board, 0, 12) == ("https://example.com/0.jpg", (0, 0, 160, 90))
    assert storyboard_cell(board, 7.9, 12)[1] == (160, 90, 160, 90)
    assert storyboard_cell(board, 8, 12) == ("https://example.com/1.jpg", (0, 0, 160, 90))
    assert storyboard_cell(board, 12, 12)[1] == (160, 0, 160, 90)


def test_preview_stream_selection_and_headers():
    info = preview_metadata(
        {
            "duration": 20,
            "http_headers": {"User-Agent": "test"},
            "formats": [
                {"vcodec": "none", "height": 0, "url": "https://example.com/audio"},
                {
                    "vcodec": "vp9",
                    "height": 1080,
                    "protocol": "https",
                    "url": "https://example.com/1080",
                },
                {
                    "vcodec": "h264",
                    "height": 360,
                    "protocol": "https",
                    "url": "https://example.com/360",
                },
            ],
        }
    )
    assert info["stream"]["url"] == "https://example.com/360"
    assert info["stream"]["headers"] == {"User-Agent": "test"}


def test_cache_lru_never_exceeds_budget():
    cache = ImageCache(16 * 16 * 4 * 2)
    image = QImage(16, 16, QImage.Format.Format_ARGB32)
    image.fill(QColor("red"))
    cache.put("a", image)
    cache.put("b", image)
    assert cache.get("a") is not None
    cache.put("c", image)
    assert cache.get("b") is None
    assert cache.size <= cache.limit


def test_cached_storyboard_crop_changes_image_without_extracting(qtbot, tmp_path):
    service, _settings, task = make_service(tmp_path)
    service.metadata[task.id] = {
        "storyboard": {
            "width": 20,
            "height": 20,
            "columns": 2,
            "rows": 1,
            "fps": 1,
            "fragments": [{"url": "https://example.com/sheet.jpg"}],
        }
    }
    sprite = QImage(40, 20, QImage.Format.Format_ARGB32)
    sprite.fill(QColor("red"))
    for y in range(20):
        for x in range(20, 40):
            sprite.setPixelColor(x, y, QColor("blue"))
    service.cache.put(("sheet", "https://example.com/sheet.jpg"), sprite)
    results = []
    service.ready.connect(lambda *args: results.append(args))
    service.request(0.3, 2)
    qtbot.waitUntil(lambda: len(results) == 1)
    assert results[-1][2].pixelColor(0, 0) == QColor("red")
    service.request(1.3, 2)
    qtbot.waitUntil(lambda: len(results) == 2)
    assert results[-1][2].pixelColor(0, 0) == QColor("blue")
    assert service.process is None
    service.cancel()


def test_hover_debounce_stale_result_and_mode_cache(qtbot, tmp_path):
    service, settings, _task = make_service(tmp_path)
    rendered, results = [], []
    service._render = lambda serial: rendered.append(serial)
    service.ready.connect(lambda *args: results.append(args))
    service.request(1.2, 20)
    old = service.serial
    service.request(5.7, 20)
    latest = service.serial
    qtbot.waitUntil(lambda: bool(rendered))
    assert rendered == [latest]
    image = QImage(16, 16, QImage.Format.Format_ARGB32)
    service._deliver(old, image)
    assert not results
    service._deliver(latest, image)
    service.request(5.9, 20)
    assert service.serial == latest  # Same one-second fast bucket.
    settings.value.thumbnail_mode = "precise"
    service.cancel()
    service.request(5.9, 20)
    assert service.frame_time == 5.9
    service.cancel()
    before = len(results)
    service._deliver(service.serial - 1, image)
    assert len(results) == before


def test_fast_storyboard_can_change_within_one_second(qapp, tmp_path):
    service, _settings, task = make_service(tmp_path)
    service.metadata[task.id] = {
        "storyboard": {
            "width": 20,
            "height": 20,
            "columns": 2,
            "rows": 2,
            "fps": 2,
            "fragments": [{"url": "https://example.com/0.jpg"}],
        }
    }
    service.request(0.1, 2)
    first = service.key
    service.request(0.6, 2)
    assert service.key != first
    service.cancel()


def test_expired_stream_refreshes_once_and_cancellation_releases_probe(qapp, tmp_path, monkeypatch):
    probes = []

    class FakeProbe(QObject):
        ready = Signal(str, dict)
        failed = Signal(str, str)

        def __init__(self, tools, task, parent):
            super().__init__(parent)
            self.timer = QTimer(self)
            self.stopped = False
            probes.append(self)

        def start(self):
            pass

        def stop(self):
            self.stopped = True
            self.timer.stop()

    monkeypatch.setattr(preview_module, "MetadataProbe", FakeProbe)
    service, settings, task = make_service(tmp_path)
    service.local = ""
    settings.value.thumbnail_mode = "precise"
    service.request(4.25, 30)
    service.timer.stop()
    serial = service.serial
    results = []
    service.ready.connect(lambda *args: results.append(args))
    service._frame_failed(serial, local=False)
    assert service.busy and len(probes) == 1
    service._render = lambda _serial: None
    probes[0].ready.emit(task.id, {"preview": {"stream": {"url": "https://example.com/new"}}})
    assert not service.busy and probes[0].stopped
    service._frame_failed(serial, local=False)
    assert len(probes) == 1 and results[-1][3] == "미리보기 불가"
    service._resolve(serial)
    assert service.busy
    service.cancel()
    assert not service.busy and probes[-1].stopped


def test_timeline_click_drag_and_hover_do_not_seek_until_release(qtbot):
    timeline = Timeline()
    timeline.resize(600, 24)
    timeline.setRange(0, 120000)
    qtbot.addWidget(timeline)
    timeline.show()
    seeks, hovers = [], []
    timeline.seek_requested.connect(seeks.append)
    timeline.hovered.connect(lambda time, _point: hovers.append(time))
    qtbot.mouseMove(timeline, QPoint(300, 12))
    assert not seeks
    qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(300, 12))
    assert len(seeks) == 1 and abs(seeks[0] - 60) < 1
    qtbot.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(150, 12))
    qtbot.mouseMove(timeline, QPoint(450, 12))
    assert len(seeks) == 1
    qtbot.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(450, 12))
    assert len(seeks) == 2 and 89 < seeks[-1] < 92
    assert timeline.value_at(-100) == 0
    assert timeline.value_at(10000) == 120000
