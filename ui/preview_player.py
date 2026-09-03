"""内嵌视频播放器：QMediaPlayer 拉取本地流服务，边下边播。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from core.models import human_size


def fmt_time(ms: int) -> str:
    s = max(0, ms // 1000)
    return f"{s // 60:02d}:{s % 60:02d}"


class VideoPreviewWidget(QWidget):
    play_toggled = Signal(bool)
    seek_requested = Signal(int)  # 拖动进度条 → 请求从该字节位置继续下载（int 字节偏移）
    stream_failed = Signal()      # QMediaPlayer 打开媒体失败（数据仍在下载，可稍后重试）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.file_name = ""
        self._size = 0
        self._buffering = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget(self)
        self.player.setVideoOutput(self.video)

        self.title = QLabel("（未在播放）")
        self.title.setStyleSheet("color:#555; padding:4px 2px;")

        self.buffer_bar = QProgressBar(self)
        self.buffer_bar.setRange(0, 1000)
        self.buffer_bar.setFixedHeight(6)
        self.buffer_bar.setTextVisible(False)
        self.buffer_label = QLabel("缓冲 0.0% · 0 B/s")
        self.buffer_label.setStyleSheet("color:#666; font-size:12px;")

        self.btn_play = QPushButton("暂停")
        self.btn_play.setFixedWidth(64)
        self.btn_play.clicked.connect(self._toggle_play)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.time_label = QLabel("00:00 / 00:00")
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(100)
        self.audio.setVolume(0.8)
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))

        ctrl = QHBoxLayout()
        ctrl.addWidget(self.btn_play)
        ctrl.addWidget(self.slider, 1)
        ctrl.addWidget(self.time_label)
        ctrl.addWidget(QLabel("音量"))
        ctrl.addWidget(self.volume)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.title)
        layout.addWidget(self.video, 1)
        layout.addWidget(self.buffer_bar)
        layout.addWidget(self.buffer_label)
        layout.addLayout(ctrl)

        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.errorOccurred.connect(self._on_player_error)
        self.slider.sliderReleased.connect(self._on_seek)
        # 点击轨道跳转不会触发 sliderPressed/Released，用「值显著偏离播放位置 +
        # 防抖」识别；等待期间暂停程序性滑块回写，避免跳转被播放位置覆盖。
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(400)
        self._click_timer.timeout.connect(self._on_click_seek)
        self.slider.valueChanged.connect(self._on_value_changed)
        self.btn_pause_text = True

    # ---------- 外部接口 ----------

    def set_waiting(self, name: str, size: int):
        """等待头部数据与索引块落盘（不开始播放，避免读到稀疏零数据/探测不到 moov）。"""
        self.file_name = name
        self._size = size
        self.player.stop()
        self.player.setSource(QUrl())
        self.slider.setRange(0, 0)
        self.title.setText(
            f"缓冲中，等待数据与索引块就绪：{name}（{human_size(size)}）")
        self.buffer_label.setText("准备中…")

    def set_stream(self, url: str, name: str, size: int):
        self.file_name = name
        self._size = size
        self.title.setText(f"正在流式播放：{name}（{human_size(size)}）")
        self.slider.setRange(0, 0)
        self.player.setSource(QUrl(url))
        self.player.play()
        self.btn_play.setText("暂停")

    def update_buffer(self, progress: float, rate: int):
        self.buffer_bar.setValue(int(progress * 1000))
        buffering = self._buffering or (progress < 0.999 and rate < 50 * 1024)
        self.buffer_label.setText(
            f"缓冲 {progress * 100:.1f}% · {human_size(rate)}/s"
            + ("  ／ 缓冲中…" if buffering else ""))
        self._buffering = False  # 事件态只提示一次，文本由本方法统一渲染

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.title.setText("（未在播放）")
        self.buffer_bar.setValue(0)
        self.buffer_label.setText("缓冲 0.0%")

    # ---------- 内部 ----------

    def _on_player_error(self, error, message: str):
        self.buffer_label.setText(f"播放器错误：{message}")
        if error != QMediaPlayer.NoError:
            self.stream_failed.emit()

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.btn_play.setText("播放")
        else:
            self.player.play()
            self.btn_play.setText("暂停")

    def _on_seek(self):
        if self.slider.maximum() > 0:
            self.player.setPosition(self.slider.value())
            self._emit_seek_byte(self.slider.value())

    def _on_value_changed(self, _v):
        """区分程序性回写与用户点击轨道：显著偏离播放位置才视为点击。"""
        if (self.slider.isSliderDown() or self.slider.maximum() <= 0
                or self._size <= 0):
            return
        if abs(self.slider.value() - self.player.position()) > 2000:
            self._click_timer.start()

    def _on_click_seek(self):
        if self.slider.isSliderDown():
            return
        v = self.slider.value()
        if abs(v - self.player.position()) <= 2000:
            return  # 已被程序性回写纠正，不是用户点击
        self.player.setPosition(v)
        self._emit_seek_byte(v)

    def _emit_seek_byte(self, position_ms: int):
        dur = self.player.duration()
        if dur > 0 and self._size > 0:
            self.seek_requested.emit(int(position_ms / dur * self._size))

    def _on_position(self, pos: int):
        # 点击轨道跳转的防抖窗口内不回写滑块，否则跳转会被播放位置覆盖
        if not self.slider.isSliderDown() and not self._click_timer.isActive():
            self.slider.setValue(pos)
        dur = self.player.duration()
        self.time_label.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")

    def _on_duration(self, dur: int):
        self.slider.setRange(0, max(0, dur))

    def _on_media_status(self, status):
        # 只记录状态，文本统一由 update_buffer 渲染，避免重复追加「缓冲中…」
        self._buffering = status in (QMediaPlayer.LoadingMedia,
                                     QMediaPlayer.BufferingMedia,
                                     QMediaPlayer.StalledMedia)
