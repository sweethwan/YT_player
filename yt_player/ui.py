"""PySide6 desktop interface; all long-running work lives in services."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .controller import Controller
from .core import QUALITIES, SettingsStore, TaskStore
from .processes import Tools
from .updates import Updater
from .widgets import PreviewPopup, Timeline, VideoWidget, clock

STATE_LABELS = {
    "queued": "대기",
    "analyzing": "주소 분석",
    "downloading": "다운로드",
    "merging": "병합",
    "verifying": "파일 검증",
    "completed": "완료",
    "failed": "실패",
    "cancelled": "중단",
    "deleted": "삭제됨",
    "cleanup_pending": "정리 대기",
}
STYLE = """
QWidget { background: #111923; color: #e4edf7; font-family: 'Segoe UI', 'Malgun Gothic';
          font-size: 13px; }
QMainWindow, QDialog { background: #111923; }
QLineEdit, QComboBox { background: #1d2a39; border: 1px solid #34465b;
                    border-radius: 6px; padding: 9px; }
QPushButton { background: #25364a; border: 1px solid #34465b; border-radius: 6px;
              padding: 9px 13px; }
QPushButton:hover { background: #344b65; }
QPushButton:disabled { color: #65768a; }
QPushButton#primary { background: #36d2ad; color: #09271f; font-weight: bold; border: none; }
QLabel#brand { font-size: 23px; font-weight: bold; }
QLabel#subtle { color: #93a8bc; }
QListWidget { background: #16212e; border: 1px solid #2c3e52;
               selection-background-color: #274e61; selection-color: #e4edf7; }
QListWidget::item { color: #e4edf7; padding: 10px; border-bottom: 1px solid #27384a; }
QListWidget::item:selected { background: #274e61; color: #e4edf7; }
QSlider::groove:horizontal { height: 5px; background: #324458; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #36d2ad; border-radius: 2px; }
QSlider::handle:horizontal { background: #d8fff4; width: 12px; margin: -5px 0; border-radius: 6px; }
QCheckBox { spacing: 8px; }
QSplitter::handle { background: #27384a; width: 6px; }
"""


class SettingsDialog(QDialog):
    preview_mode_changed = Signal()

    def __init__(self, settings: SettingsStore, updater: Updater, parent=None):
        super().__init__(parent)
        self.settings, self.updater = settings, updater
        self.setWindowTitle("설정 · 변경 사항 자동 저장")
        self.resize(660, 420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(16)
        path_row = QHBoxLayout()
        self.path = QLineEdit(settings.value.download_dir)
        self.path.setReadOnly(True)
        browse = QPushButton("폴더 선택")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        form.addRow("영상 저장 폴더", path_row)
        self.quality = QComboBox()
        for value in QUALITIES:
            self.quality.addItem("자동 최고 화질" if value == "best" else f"최대 {value}p", value)
        self.quality.setCurrentIndex(QUALITIES.index(settings.value.quality))
        self.quality.currentIndexChanged.connect(
            lambda: settings.update(quality=self.quality.currentData())
        )
        form.addRow("영상 화질", self.quality)
        self.seek_step = QSpinBox()
        self.seek_step.setRange(1, 3599)
        self.seek_step.setSuffix(" 초")
        self.seek_step.setValue(settings.value.seek_seconds)
        self.seek_large_step = QSpinBox()
        self.seek_large_step.setRange(settings.value.seek_seconds + 1, 3600)
        self.seek_large_step.setSuffix(" 초")
        self.seek_large_step.setValue(settings.value.seek_large_seconds)
        self.seek_step.valueChanged.connect(self._seek_settings)
        self.seek_large_step.valueChanged.connect(self._seek_settings)
        form.addRow("← / → 이동 간격", self.seek_step)
        form.addRow("Shift + ← / → 이동 간격", self.seek_large_step)
        self.preview_mode = QComboBox()
        self.preview_mode.addItem("빠른 미리보기", "fast")
        self.preview_mode.addItem("정밀 미리보기", "precise")
        self.preview_mode.setCurrentIndex(self.preview_mode.findData(settings.value.thumbnail_mode))
        self.preview_mode.currentIndexChanged.connect(self._preview_settings)
        form.addRow("타임바 썸네일", self.preview_mode)
        preview_note = QLabel("정밀 방식은 표시 지연과 추가 스트리밍 트래픽이 발생할 수 있습니다.")
        preview_note.setWordWrap(True)
        preview_note.setObjectName("subtle")
        form.addRow("", preview_note)
        delete = QCheckBox("새 영상 재생 시작 시 직전 영상과 임시 파일 삭제")
        delete.setChecked(settings.value.auto_delete)
        delete.toggled.connect(lambda value: settings.update(auto_delete=value))
        form.addRow("자동 삭제", delete)
        check = QCheckBox("시작 시 yt-dlp 업데이트 확인 (24시간 캐시)")
        check.setChecked(settings.value.check_updates)
        check.toggled.connect(lambda value: settings.update(check_updates=value))
        form.addRow("업데이트", check)
        self.versions = QLabel()
        self.versions.setWordWrap(True)
        self.update_status = QLabel()
        self.update_status.setWordWrap(True)
        form.addRow("yt-dlp", self.versions)
        form.addRow("상태", self.update_status)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.check_button = QPushButton("업데이트 확인")
        self.update_button = QPushButton("업데이트")
        self.check_button.clicked.connect(lambda: updater.check(force=True))
        self.update_button.clicked.connect(updater.request_install)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.update_button)
        buttons.addStretch()
        close = QPushButton("닫기")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addStretch()
        hint = QLabel(
            "설정은 자동 저장됩니다. 화질·저장 폴더·삭제 설정은 다음 작업부터 적용됩니다."
        )
        hint.setObjectName("subtle")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addLayout(buttons)
        updater.changed.connect(self.refresh)
        self.refresh()

    def _seek_settings(self):
        self.seek_large_step.blockSignals(True)
        self.seek_large_step.setMinimum(self.seek_step.value() + 1)
        self.seek_large_step.blockSignals(False)
        self.settings.update(
            seek_seconds=self.seek_step.value(), seek_large_seconds=self.seek_large_step.value()
        )

    def _preview_settings(self):
        self.settings.update(thumbnail_mode=self.preview_mode.currentData())
        self.preview_mode_changed.emit()

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "영상 저장 폴더", self.path.text())
        if folder:
            self.path.setText(folder)
            self.settings.update(download_dir=folder)

    def refresh(self):
        u = self.updater
        self.versions.setText(
            f"현재 {u.current}  /  최신 {u.latest or '미확인'}\n"
            f"마지막 확인: {u.last_check or '없음'}"
        )
        self.update_status.setText(u.status or "대기")
        self.check_button.setEnabled(u.worker is None and not u.applying)
        self.update_button.setEnabled(u.worker is None and not u.applying and not u.pending)


class MainWindow(QMainWindow):
    def __init__(self, tools: Tools, settings: SettingsStore, store: TaskStore):
        super().__init__()
        self.tools, self.settings, self.store = tools, settings, store
        self.setWindowTitle("YT Player · YouTube 다운로드 및 재생")
        self.setMinimumSize(800, 600)
        self.resize(settings.value.width, settings.value.height)
        self.list_ids: list[str] = []
        self.fullscreen = False
        self.pending_close = False
        self.normal_geometry = None
        self.normal_maximized = False
        self.dialog: SettingsDialog | None = None
        self._build()
        self.controller = Controller(tools, settings, store, int(self.video.winId()), self)
        self.player = self.controller.player
        self.video_click_timer = QTimer(self)
        self.video_click_timer.setSingleShot(True)
        self.video_click_timer.timeout.connect(self._commit_video_click)
        self.double_click_guard = QTimer(self)
        self.double_click_guard.setSingleShot(True)
        self.click_generation = -1
        self.preview_popup = PreviewPopup(self)
        self.player.input_action.connect(self._input_action)
        self.video.clicked.connect(self._video_click)
        self.video.double_clicked.connect(self._video_double_click)
        self.controller.previews.ready.connect(self._preview_ready)
        self.controller.previews.invalidated.connect(self.preview_popup.hide)
        self.seek.hovered.connect(self._preview_hover)
        self.seek.hover_left.connect(self.controller.previews.cancel)
        QApplication.instance().installEventFilter(self)
        self.player.volume = settings.value.volume
        self.player.muted = settings.value.muted
        self.player.speed = settings.value.speed
        self.updater = Updater(tools, settings, self.controller.busy, self)
        self.controller.changed.connect(self.refresh_task)
        self.controller.message.connect(self.notice)
        self.controller.selected.connect(self.select_task)
        self.controller.list_cleared.connect(self._list_cleared)
        self.player.status.connect(self.play_status.setText)
        self.player.position.connect(self._position)
        self.player.ended.connect(lambda _: self.play_status.setText("재생 완료"))
        self.updater.changed.connect(self._update_changed)
        self.updater.message.connect(self.notice)
        settings.error.connect(self.notice)
        store.error.connect(self.notice)
        self.submit.clicked.connect(self._submit)
        self.url.returnPressed.connect(self._submit)
        self.settings_button.clicked.connect(self._settings)
        self.pause.clicked.connect(self._toggle_playback)
        self.stop.clicked.connect(self.controller.stop_playback)
        self.seek.seek_requested.connect(self.player.seek)
        self.volume.valueChanged.connect(self._volume)
        self.mute.toggled.connect(self._mute)
        self.speed.currentIndexChanged.connect(self._speed)
        self.full_button.clicked.connect(self._fullscreen)
        self.cancel.clicked.connect(self._cancel)
        self.retry.clicked.connect(self._retry)
        self.folder.clicked.connect(self._folder)
        self.clear_button.clicked.connect(self.controller.clear_list)
        self.task_list.itemDoubleClicked.connect(self._open_item)
        self.task_list.itemSelectionChanged.connect(self._selection_changed)
        self.splitter.splitterMoved.connect(self._sidebar_resized)
        QTimer.singleShot(
            0, self, lambda: self._set_sidebar_width(self.settings.value.sidebar_width)
        )
        for sequence, action in [
            ("Space", self._toggle_playback),
            ("F11", self._fullscreen),
            ("Escape", self._escape),
        ]:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(action)
        self._refresh_list()
        QTimer.singleShot(0, self, self.controller.recover_cleanup)
        QTimer.singleShot(100, self, self.updater.initialize)
        warnings = settings.warnings + store.warnings
        if tools.missing():
            warnings.append(
                "필수 도구 미설치: "
                + ", ".join(tools.missing())
                + " · README의 도구 준비 명령을 실행하세요."
            )
        if warnings:
            QTimer.singleShot(200, self, lambda: self.notice("\n".join(warnings)))

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        self.heading = QWidget()
        heading = QHBoxLayout(self.heading)
        heading.setContentsMargins(0, 0, 0, 0)
        brand = QLabel("YT Player")
        brand.setObjectName("brand")
        subtitle = QLabel("보고, 저장하고, 다시 재생하세요")
        subtitle.setObjectName("subtle")
        heading.addWidget(brand)
        heading.addWidget(subtitle)
        heading.addStretch()
        self.settings_button = QPushButton("설정")
        heading.addWidget(self.settings_button)
        layout.addWidget(self.heading)
        self.address_row = QWidget()
        address = QHBoxLayout(self.address_row)
        address.setContentsMargins(0, 0, 0, 0)
        self.url = QLineEdit()
        self.url.setPlaceholderText("YouTube 영상 또는 Shorts 주소를 붙여넣고 Enter")
        self.url.setClearButtonEnabled(True)
        self.submit = QPushButton("다운로드 및 재생")
        self.submit.setObjectName("primary")
        address.addWidget(self.url, 1)
        address.addWidget(self.submit)
        layout.addWidget(self.address_row)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.sidebar = QWidget()
        self.sidebar.setMinimumWidth(260)
        self.sidebar.setMaximumWidth(520)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 0, 12, 0)
        now_playing = QLabel("현재 영상")
        now_playing.setObjectName("subtle")
        sidebar_layout.addWidget(now_playing)
        self.title = QLabel("영상을 선택하세요")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        self.title.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.title.setMaximumHeight(80)
        sidebar_layout.addWidget(self.title)
        self.play_status = QLabel("재생 대기")
        self.play_status.setWordWrap(True)
        self.play_status.setObjectName("subtle")
        sidebar_layout.addWidget(self.play_status)
        self.list_count = QLabel("최근 영상 · 0 / 20")
        sidebar_layout.addWidget(self.list_count)
        self.task_list = QListWidget()
        self.task_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.task_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.task_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_layout.addWidget(self.task_list, 1)
        self.task_buttons = QWidget()
        buttons = QVBoxLayout(self.task_buttons)
        buttons.setContentsMargins(0, 0, 0, 0)
        self.cancel, self.retry, self.folder, self.clear_button = (
            QPushButton(text)
            for text in ["선택 다운로드 취소", "선택 재시도", "저장 폴더 열기", "리스트 비우기"]
        )
        self.clear_button.setToolTip(
            "모든 다운로드·주소 분석 취소 및 목록 비우기 · 재생과 파일 유지"
        )
        for button in [self.cancel, self.retry, self.folder, self.clear_button]:
            buttons.addWidget(button)
        sidebar_layout.addWidget(self.task_buttons)
        self.splitter.addWidget(self.sidebar)
        self.media_pane = QWidget()
        media = QVBoxLayout(self.media_pane)
        media.setContentsMargins(12, 0, 0, 0)
        self.video = VideoWidget()
        self.video.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.video.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.video.setStyleSheet("background: #05090e;")
        self.video.setMinimumHeight(160)
        media.addWidget(self.video, 1)
        self.seek = Timeline()
        media.addWidget(self.seek)
        controls = QHBoxLayout()
        self.pause = QPushButton("재생 / 일시정지")
        self.stop = QPushButton()
        stop_pixmap = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop).pixmap(20, 20)
        painter = QPainter(stop_pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(stop_pixmap.rect(), QColor("#d8fff4"))
        painter.end()
        self.stop.setIcon(QIcon(stop_pixmap))
        self.stop.setIconSize(QSize(20, 20))
        self.stop.setFixedWidth(42)
        self.stop.setToolTip("재생 중지")
        self.stop.setAccessibleName("재생 중지")
        self.time = QLabel("00:00 / 00:00")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(self.settings.value.volume)
        self.volume.setFixedWidth(105)
        self.mute = QPushButton("음소거")
        self.mute.setCheckable(True)
        self.mute.setChecked(self.settings.value.muted)
        self.speed = QComboBox()
        for value in [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]:
            self.speed.addItem(f"{value:g}×", value)
        self.speed.setCurrentIndex(self.speed.findData(self.settings.value.speed))
        self.full_button = QPushButton("전체화면")
        for widget in [self.pause, self.stop, self.time]:
            controls.addWidget(widget)
        controls.addStretch()
        controls.addWidget(self.full_button)
        media.addLayout(controls)
        audio_controls = QHBoxLayout()
        for widget in [self.mute, self.volume, self.speed]:
            audio_controls.addWidget(widget)
        audio_controls.addStretch()
        media.addLayout(audio_controls)
        self.splitter.addWidget(self.media_pane)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter, 1)
        self.update_hint = QLabel("yt-dlp")
        self.statusBar().addPermanentWidget(self.update_hint)

    def _submit(self):
        if self.updater.applying:
            self.notice("yt-dlp 업데이트 적용 중입니다. 완료 후 실행하세요.")
            return
        self.controller.open(self.url.text())

    def _toggle_playback(self):
        if self.updater.applying:
            self.notice("yt-dlp 업데이트 적용 중입니다. 완료 후 재생하세요.")
            return
        self.controller.toggle_playback()

    def _input_action(self, action):
        if self.dialog is not None and self.dialog.isVisible():
            return
        if action == "video-click":
            self._video_click()
        elif action == "video-double-click":
            self._video_double_click()
        elif action == "fullscreen-toggle":
            self._fullscreen()
        elif action == "fullscreen-exit":
            self._escape()
        elif action == "pause":
            self._toggle_playback()
        elif action in {"seek-back", "seek-forward", "seek-back-large", "seek-forward-large"}:
            step = (
                self.settings.value.seek_large_seconds
                if action.endswith("-large")
                else self.settings.value.seek_seconds
            )
            self.player.seek_relative(-step if action.startswith("seek-back") else step)

    def _video_click(self):
        if (
            self.double_click_guard.isActive()
            or not self.player.has_file
            or not self.player.ready
            or (self.dialog is not None and self.dialog.isVisible())
        ):
            return
        self.click_generation = self.player.generation
        # Wait for the second press before committing a single click. Both the
        # native mpv window and Qt use the same system double-click interval.
        self.video_click_timer.start(QApplication.doubleClickInterval() + 50)

    def _commit_video_click(self):
        if (
            self.click_generation == self.player.generation
            and self.player.has_file
            and self.player.ready
            and not (self.dialog is not None and self.dialog.isVisible())
        ):
            self._toggle_playback()

    def _video_double_click(self):
        self.video_click_timer.stop()
        # Some native backends deliver a trailing single-click release.
        self.double_click_guard.start(QApplication.doubleClickInterval())
        self._fullscreen()

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress}:
            if (
                isinstance(watched, QWidget)
                and watched.window() is self
                and event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Right}
                and not (self.dialog is not None and self.dialog.isVisible())
                and not isinstance(QApplication.focusWidget(), (QLineEdit, QSpinBox, QComboBox))
                and not (
                    event.modifiers()
                    & (
                        Qt.KeyboardModifier.ControlModifier
                        | Qt.KeyboardModifier.AltModifier
                        | Qt.KeyboardModifier.MetaModifier
                    )
                )
            ):
                if event.type() == QEvent.Type.KeyPress:
                    action = "seek-back" if event.key() == Qt.Key.Key_Left else "seek-forward"
                    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        action += "-large"
                    self._input_action(action)
                event.accept()
                return True
        if watched is self and event.type() == QEvent.Type.WindowDeactivate:
            if hasattr(self, "video_click_timer"):
                self.video_click_timer.stop()
            if hasattr(self, "controller"):
                self.controller.previews.cancel()
        return super().eventFilter(watched, event)

    def _preview_hover(self, seconds, anchor):
        if not self.player.has_file or self.player.duration <= 0:
            return
        self.preview_popup.loading()
        self.preview_popup.present(seconds, anchor)
        self.controller.previews.request(seconds, self.player.duration, self.player.fps)

    def _preview_ready(self, serial, _seconds, image, error):
        if serial == self.controller.previews.serial and self.preview_popup.isVisible():
            self.preview_popup.display(image, error)

    def refresh_task(self, task_id):
        self._refresh_list()

    def _refresh_list(self):
        selected = self._selected()
        recent = self.store.recent_tasks()
        ids = [task.id for task in recent]
        if ids != self.list_ids:
            scroll = self.task_list.verticalScrollBar().value()
            self.task_list.blockSignals(True)
            self.task_list.clear()
            for task in recent:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, task.id)
                item.setSizeHint(QSize(0, 68))
                self.task_list.addItem(item)
            self.list_ids = ids
            if selected in ids:
                self.task_list.setCurrentRow(ids.index(selected))
            self.task_list.verticalScrollBar().setValue(scroll)
            self.task_list.blockSignals(False)
        for row, task in enumerate(recent):
            state = STATE_LABELS.get(task.state, task.state)
            title = " ".join(task.title.split())
            item = self.task_list.item(row)
            item.setText(f"{title}\n{state} · {task.progress:.1f}%")
            speed = f"{task.speed / 1024 / 1024:.1f} MB/s"
            quality = "최고" if task.quality == "best" else task.quality + "p"
            item.setToolTip(
                f"{task.title}\n{state} · {task.progress:.1f}%\n"
                f"화질: {quality} · 속도: {speed} · 남은 시간: {clock(task.eta)}"
                + (f"\n{task.error}" if task.error else "")
            )
        if current := self.store.tasks.get(self.controller.current_id):
            self.title.setText(current.title)
            self.title.setToolTip(current.title)
        self.list_count.setText(f"최근 영상 · {len(recent)} / 20")
        self._selection_changed()

    def select_task(self, task_id):
        self.refresh_task(task_id)
        if task_id in self.list_ids:
            self.task_list.setCurrentRow(self.list_ids.index(task_id))
            self.task_list.scrollToItem(self.task_list.currentItem())

    def _selected(self):
        item = self.task_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selection_changed(self):
        task = self.store.tasks.get(self._selected())
        self.cancel.setEnabled(
            task is not None
            and task.state in {"queued", "analyzing", "downloading", "merging", "verifying"}
        )
        self.retry.setEnabled(
            task is not None and task.state in {"failed", "cancelled"} and not task.delete_pending
        )

    def _list_cleared(self):
        self.task_list.setCurrentRow(-1)
        self._refresh_list()
        self.notice("목록을 비우고 모든 다운로드를 취소했습니다. 현재 재생과 파일은 유지됩니다.")

    def _sidebar_resized(self, _position, _index):
        if not self.fullscreen:
            self.settings.update(sidebar_width=self.splitter.sizes()[0])

    def _set_sidebar_width(self, width):
        remaining = self.splitter.width() - self.splitter.handleWidth() - width
        self.splitter.setSizes([width, max(1, remaining)])

    def _cancel(self):
        if task_id := self._selected():
            self.controller.cancel(task_id)

    def _retry(self):
        if self.updater.applying:
            return
        if task_id := self._selected():
            self.controller.retry(task_id)

    def _folder(self):
        task_id = self._selected()
        path = (
            Path(self.store.tasks[task_id].folder)
            if task_id
            else Path(self.settings.value.download_dir)
        )
        if not path.exists():
            path = path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_item(self, item):
        if self.updater.applying:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        task = self.store.tasks[task_id]
        self.url.setText(task.url)
        self._submit()

    def _position(self, position, duration):
        if not self.seek.isSliderDown():
            self.seek.setRange(0, min(2147483647, int(duration * 1000)))
            self.seek.setValue(int(position * 1000))
        self.time.setText(f"{clock(position)} / {clock(duration)}")

    def _volume(self, value):
        self.player.set_volume(value)
        self.settings.update(volume=value)

    def _mute(self, value):
        self.player.set_muted(value)
        self.settings.update(muted=value)

    def _speed(self):
        value = self.speed.currentData()
        self.player.set_speed(value)
        self.settings.update(speed=value)

    def _settings(self):
        self.video_click_timer.stop()
        if self.dialog is None:
            self.dialog = SettingsDialog(self.settings, self.updater, self)
            self.dialog.preview_mode_changed.connect(self.controller.previews.cancel)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def _fullscreen(self):
        self.video_click_timer.stop()
        self.controller.previews.cancel()
        if not self.fullscreen:
            self.normal_geometry = self.saveGeometry()
            self.normal_maximized = self.isMaximized()
            self.normal_sidebar_width = self.splitter.sizes()[0]
        self.fullscreen = not self.fullscreen
        for widget in [self.heading, self.address_row, self.sidebar]:
            widget.setVisible(not self.fullscreen)
        if self.fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()
            if self.normal_geometry is not None:
                self.restoreGeometry(self.normal_geometry)
            if self.normal_maximized:
                self.showMaximized()
            self._set_sidebar_width(self.normal_sidebar_width)

    def _escape(self):
        if self.fullscreen:
            self._fullscreen()

    def _update_changed(self):
        self.update_hint.setText(f"yt-dlp {self.updater.current}")
        self.update_hint.setToolTip(self.updater.status)
        if self.pending_close and self.updater.worker is None:
            QTimer.singleShot(0, self.close)

    def notice(self, message):
        logging.warning("%s", message)
        self.statusBar().showMessage(message.replace("\n", " ")[:350], 20000)
        # Full error details remain accessible without interrupting playback.
        self.play_status.setToolTip(message)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "settings") and not self.isFullScreen() and not self.isMaximized():
            self.settings.update(width=self.width(), height=self.height())

    def closeEvent(self, event):
        self.video_click_timer.stop()
        if self.updater.worker is not None:
            self.pending_close = True
            self.updater.pending = False
            self.notice("업데이트 작업이 끝나면 자동으로 종료합니다.")
            event.ignore()
            return
        self.updater.poll.stop()
        self.controller.shutdown()
        self.settings.flush()
        event.accept()
