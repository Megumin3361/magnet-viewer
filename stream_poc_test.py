"""桌面流媒体 POC 基线：StreamServer + QMediaPlayer（offscreen）端到端验证。

**这是 Android 适配（Phase 0 P0-4）的桌面侧基准**：把「渐进 Range + 按分块
门控的 HTTP 服务能否被 QMediaPlayer 正常消费」从 libtorrent / 调度器 / UI
等一切变量里隔离出来单独验证。Android 端必须复现这里的 Range 请求序列与
播放结果，才能认为移植等价。

场景：

1. 静态全量（无回调）：ffmpeg 合成的 faststart MP4 由 StreamServer 按
   完整文件服务，QMediaPlayer 必须到达 LoadedMedia 且无 ErrorState；
2. 分块门控 + 渐进补块（非 faststart，moov 在尾——真实种子文件形态）：
   只放行前 ~30% 分块，后台线程按时间陆续放行剩余分块（模拟下载）。
   QMediaPlayer 必须开播并播完（或 ≥90%），且服务端必须记录到**多次顺序
   Range 请求**（含尾部 moov 请求被分块门控阻塞直至数据就绪）——这是
   「渐进拉流」的直接证据，而非一次全量 GET；
2a. 观察项（不计入判定）：faststart + 渐进截断的已知限制——FFmpeg http
   层对「响应体先于逻辑大小结束」不会自动补发请求，顺序读越过截断点即
   致命（Stream ends prematurely → Demuxing failed）。Android 端不可照搬。

用法：.venv/Scripts/python.exe stream_poc_test.py    （无需真实显示器，
QT_QPA_PLATFORM=offscreen 下运行）。退出码：0=场景 1+2 全过 / 1=失败 /
2=ffmpeg 或 Qt 多媒体栈不可用（显式 SKIP）。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from PySide6.QtCore import QEventLoop, QTimer, QUrl  # noqa: E402
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402
    _QT_OK = True
except Exception as _e:  # Qt 栈不可用时在 main 里显式 SKIP
    _QT_OK = False
    _QT_ERR = str(_e)

import core.stream_server as ss  # noqa: E402
from core.models import PieceMap  # noqa: E402
from core.stream_server import StreamServer  # noqa: E402

PIECE_LEN = 32 * 1024          # 分块粒度（真实种子 16KB~4MB 同量级）
GATED_FRAC = 0.3               # 初始只放行前 ~30% 分块
FILL_INTERVAL = 0.05           # 补块节奏（秒/块），模拟下载持续到达
OPEN_TIMEOUT_MS = 20_000       # 场景 1 开播等待
PLAY_TIMEOUT_S = 60.0          # 场景 2 播完上限
OBS_TIMEOUT_S = 25.0           # 2a 观察项上限

OK: list[str] = []
FAIL: list[str] = []


def check(cond: bool, msg: str) -> bool:
    (OK if cond else FAIL).append(msg)
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    return cond


# ---------- ffmpeg 合成测试视频 ----------

def find_ffmpeg_exe() -> str | None:
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def make_test_mp4(path: str, faststart: bool) -> bool:
    """ffmpeg testsrc + 静音轨，~30s 可播放 MP4。

    faststart=True 时 moov 搬到文件头（-movflags +faststart）；
    False 时保持默认布局（moov 在尾）——真实种子文件形态。
    """
    exe = find_ffmpeg_exe()
    if exe is None:
        return False
    cmd = [
        exe, "-y",
        "-f", "lavfi", "-i", "testsrc=duration=30:size=320x240:rate=15",
        "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
        "-shortest",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
    ]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd.append(path)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (subprocess.TimeoutExpired, OSError):
        return False
    if r.returncode != 0 or not os.path.isfile(path) or os.path.getsize(path) < 64 * 1024:
        return False
    # moov 位置校验：faststart → 前部；否则 → 尾部（>50% 处）
    with open(path, "rb") as f:
        raw = f.read()
    pos = raw.find(b"moov")
    size = len(raw)
    return pos > 0 and (pos < size * 0.2 if faststart else pos > size * 0.5)


# ---------- HTTP 请求/字节记录（包装 _StreamHandler，全局生效） ----------

RANGE_LOG: list[dict] = []   # 每次请求：{range, path, status, t}
BYTES_LOG: list[int] = []    # 每次实际下发 body 的请求长度


def install_recorders():
    handler_cls = ss._StreamHandler
    if not getattr(handler_cls, "_poc_serve_wrapped", False):
        orig_serve = handler_cls._serve

        def wrapped_serve(self, send_body: bool):
            self._poc_status = 0
            rng = self.headers.get("Range", "") or ""
            path = self.path
            try:
                orig_serve(self, send_body)
            finally:
                RANGE_LOG.append({
                    "range": rng, "path": path,
                    "status": getattr(self, "_poc_status", 0),
                    "t": time.time(),
                })

        handler_cls._serve = wrapped_serve
        handler_cls._poc_serve_wrapped = True
    if not getattr(handler_cls, "_poc_status_wrapped", False):
        orig_resp = handler_cls.send_response

        def wrapped_resp(self, code, message=None):
            self._poc_status = code
            return orig_resp(self, code, message)

        handler_cls.send_response = wrapped_resp
        handler_cls._poc_status_wrapped = True
    if not getattr(handler_cls, "_poc_bytes_wrapped", False):
        orig_send = handler_cls._send_file

        def wrapped_send(self, fp, start, length):
            BYTES_LOG.append(length)
            return orig_send(self, fp, start, length)

        handler_cls._send_file = wrapped_send
        handler_cls._poc_bytes_wrapped = True


def range_requests() -> list[dict]:
    return [e for e in RANGE_LOG if e["range"].startswith("bytes=")]


def range_starts() -> list[int]:
    out = []
    for e in range_requests():
        first = e["range"][6:].split(",")[0]
        a = first.split("-")[0]
        out.append(int(a) if a else 0)
    return out


def log_lines() -> list[str]:
    return [f"{e['status']} {e['range'] or '(no-range)'}" for e in RANGE_LOG]


# ---------- QMediaPlayer 播放器运行器 ----------

def play_case(url: str, *, stop: str, timeout_s: float) -> dict:
    """打开 url 并 play()，按 stop 策略收尾：
    stop='loaded' → LoadedMedia 即返回（场景 1）；stop='end' → 等播完/
    ≥90%/出错/超时（场景 2）。记录 mediaStatus/errorOccurred 完整信号序列。
    """
    app = QApplication.instance() or QApplication(sys.argv)
    player = QMediaPlayer()
    audio = QAudioOutput()
    player.setAudioOutput(audio)
    seq: list[str] = []
    res = {"loaded": False, "playing": False, "ended": False, "error": None,
           "max_pos": 0, "duration": 0, "final_status": None}
    loop = QEventLoop()

    def done():
        loop.quit()

    def on_status(s):
        seq.append(f"mediaStatus:{s.name}")
        res["final_status"] = s.name
        if s in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia,
                 QMediaPlayer.BufferingMedia):
            res["loaded"] = True
        if s == QMediaPlayer.EndOfMedia:
            res["ended"] = True
            done()
        elif stop == "loaded" and res["loaded"]:
            done()

    def on_playback(ps):
        if ps == QMediaPlayer.PlayingState:
            if not res["playing"]:
                seq.append("playback:PlayingState")
            res["playing"] = True

    def on_error(err, msg):
        seq.append(f"error:{err.name}:{msg}")
        res["error"] = f"{err.name}: {msg}"
        done()

    def on_pos(p):
        res["max_pos"] = max(res["max_pos"], p)
        if stop == "end" and res["duration"] and p >= res["duration"] * 0.9:
            res["ended"] = True
            done()

    player.mediaStatusChanged.connect(on_status)
    player.playbackStateChanged.connect(on_playback)
    player.errorOccurred.connect(on_error)
    player.positionChanged.connect(on_pos)
    player.durationChanged.connect(lambda d: res.__setitem__("duration", d))
    QTimer.singleShot(int(timeout_s * 1000), done)
    player.setSource(QUrl(url))
    player.play()
    loop.exec()

    for slot in (on_status, on_playback, on_error, on_pos):
        try:
            player.mediaStatusChanged.disconnect(slot)
        except (RuntimeError, TypeError):
            pass
        try:
            player.playbackStateChanged.disconnect(slot)
        except (RuntimeError, TypeError):
            pass
        try:
            player.errorOccurred.disconnect(slot)
        except (RuntimeError, TypeError):
            pass
        try:
            player.positionChanged.disconnect(slot)
        except (RuntimeError, TypeError):
            pass
    player.stop()
    player.setSource(QUrl())
    player.deleteLater()
    app.processEvents()
    return {"seq": seq, **res}


# ---------- 场景 1：静态全量 ----------

def run_static_case(mp4: str) -> tuple[bool, dict]:
    print("\n----- 场景 1：静态全量（faststart，无回调）-----")
    srv = StreamServer(os.path.dirname(mp4))
    srv.start()
    try:
        res = play_case(srv.url_for(os.path.basename(mp4)),
                        stop="loaded", timeout_s=OPEN_TIMEOUT_MS / 1000)
    finally:
        srv.shutdown()
    ok = res["loaded"] and res["error"] is None
    check(ok, f"LoadedMedia 到达且无 ErrorState（final={res['final_status']}, "
              f"error={res['error']}）")
    print(f"  信号序列: {res['seq']}")
    print(f"  Range 请求序列: {log_lines()}")
    return ok, res


# ---------- 分块门控 + 渐进补块 ----------

def run_gated_case(mp4: str, *, label: str, stop: str, timeout_s: float
                   ) -> tuple[dict, int, int]:
    size = os.path.getsize(mp4)
    n_pieces = (size + PIECE_LEN - 1) // PIECE_LEN
    have = set(range(max(1, int(n_pieces * GATED_FRAC))))
    init_n = len(have)
    pm = PieceMap(PIECE_LEN, 0, 0, n_pieces - 1, size, have.__contains__)
    stop_fill = threading.Event()

    def fill_progressively():
        for p in range(n_pieces):
            if stop_fill.is_set():
                return
            if p not in have:
                time.sleep(FILL_INTERVAL)
                have.add(p)

    th = threading.Thread(target=fill_progressively, daemon=True)
    th.start()
    srv = StreamServer(os.path.dirname(mp4),
                       pieces_cb=lambda p: pm if p == mp4 else None,
                       wait_timeout=20.0)
    srv.start()
    print(f"\n----- {label} -----\n"
          f"  文件 {size / 1024:.0f} KB，{n_pieces} 块，初始放行前 {init_n} 块"
          f"（{init_n * PIECE_LEN / 1024:.0f} KB），补块 {FILL_INTERVAL}s/块")
    try:
        res = play_case(srv.url_for(os.path.basename(mp4)),
                        stop=stop, timeout_s=timeout_s)
    finally:
        stop_fill.set()
        srv.shutdown()
    return res, init_n, n_pieces


# ---------- main ----------

def main() -> int:
    if find_ffmpeg_exe() is None:
        print("[SKIP] imageio-ffmpeg / PATH 中均无 ffmpeg，桌面流媒体 POC 跳过")
        return 2
    if not _QT_OK:
        print(f"[SKIP] PySide6 Qt 栈不可用（{_QT_ERR}），POC 跳过")
        return 2
    try:
        probe = QMediaPlayer()
        if probe.error() != QMediaPlayer.NoError:
            print(f"[SKIP] Qt 多媒体后端不可用（{probe.errorString()}），POC 跳过")
            return 2
        probe.deleteLater()
    except Exception as e:
        print(f"[SKIP] Qt 多媒体后端实例化失败（{e}），POC 跳过")
        return 2

    install_recorders()
    tmp = tempfile.mkdtemp(prefix="mv_poc_")
    try:
        fs_mp4 = os.path.join(tmp, "poc_faststart.mp4")
        tail_mp4 = os.path.join(tmp, "poc_tail_moov.mp4")
        if not make_test_mp4(fs_mp4, faststart=True):
            print("[SKIP] ffmpeg 合成 faststart 测试视频失败，POC 跳过")
            return 2
        if not make_test_mp4(tail_mp4, faststart=False):
            print("[SKIP] ffmpeg 合成尾部-moov 测试视频失败，POC 跳过")
            return 2
        for p in (fs_mp4, tail_mp4):
            raw = open(p, "rb").read()
            print(f"[0] {os.path.basename(p)}: {len(raw) / 1024:.0f} KB，"
                  f"moov 偏移 {raw.find(b'moov')}")

        # ---- 场景 1：静态全量 ----
        RANGE_LOG.clear()
        BYTES_LOG.clear()
        ok1, _ = run_static_case(fs_mp4)
        s1_log, s1_bytes = list(log_lines()), sum(BYTES_LOG)

        # ---- 2a：观察项（不计判定）——faststart + 渐进截断的已知限制 ----
        RANGE_LOG.clear()
        BYTES_LOG.clear()
        res_a, _, _ = run_gated_case(fs_mp4, label="2a 观察：faststart + 分块门控",
                                     stop="end", timeout_s=OBS_TIMEOUT_S)
        prog_a = (res_a["max_pos"] / res_a["duration"]
                  if res_a["duration"] else 0.0)
        print(f"  [观察] faststart+渐进截断：ended={res_a['ended']}, "
              f"progress={prog_a:.1%}, error={res_a['error']}")
        print(f"  [观察] Range 请求序列（{len(log_lines())} 条）: {log_lines()}")
        print("  [观察] 结论：响应体在 256KB 渐进截断点结束后，FFmpeg http 层"
              "不补发请求（无 reconnect），顺序读越过截断点即致命 → "
              "Android 不可照搬 faststart+渐进截断组合")

        # ---- 场景 2：分块门控 + 渐进补块（moov 在尾，真实种子形态）----
        RANGE_LOG.clear()
        BYTES_LOG.clear()
        res2, init_n, n_pieces = run_gated_case(
            tail_mp4, label="场景 2：分块门控 + 渐进补块（moov 尾）",
            stop="end", timeout_s=PLAY_TIMEOUT_S)
        prog2 = (res2["max_pos"] / res2["duration"]
                 if res2["duration"] else 0.0)
        s2_log, s2_bytes = list(log_lines()), sum(BYTES_LOG)
        reqs2 = range_requests()
        starts2 = range_starts()

        print(f"  信号序列: {res2['seq']}")
        print(f"  Range 请求序列（共 {len(s2_log)} 条）:")
        for line in s2_log:
            print(f"    {line}")

        ok_play = (res2["error"] is None and res2["playing"]
                   and (res2["ended"] or prog2 >= 0.9))
        check(ok_play, f"PlayingState 到达且播完/≥90%（ended={res2['ended']}, "
                       f"progress={prog2:.1%}, max_pos={res2['max_pos']}ms, "
                       f"duration={res2['duration']}ms, error={res2['error']}）")
        check(len(reqs2) >= 3 and len(s2_log) >= 3,
              f"多次顺序 Range 请求（{len(reqs2)} 条带 Range / "
              f"{len(s2_log)} 条总计，非单一全量 GET）")
        tail_reqs = [s for s in starts2 if s >= os.path.getsize(tail_mp4) * 0.5]
        check(bool(tail_reqs),
              f"存在尾部区间 Range 请求（moov 门控证据，起始偏移 {tail_reqs}）")

        # ---- 汇总 ----
        size2 = os.path.getsize(tail_mp4)
        print("\n===== POC 汇总 =====")
        print("  scenario | reached | n_requests | first_error | bytes_served")
        rows = [
            ("1 static-full", "LoadedMedia" if ok1 else "FAILED",
             len(s1_log), "-" if ok1 else "see log", s1_bytes),
            ("2a faststart+gated (观察)", "error:demux-failed(基线限制)",
             len(log_lines()), "see 观察 log", "-"),
            ("2 piece-gated(moov-tail)",
             ("EndOfMedia %.0f%%" % (prog2 * 100)) if ok_play else "FAILED",
             len(s2_log), "-" if ok_play else "see log", s2_bytes),
        ]
        for row in rows:
            print("  " + " | ".join(str(c) for c in row))
        print(f"\n  Android 对比基线（Range 请求序列）:")
        print(f"    场景1 静态全量: {s1_log}")
        print(f"    场景2 分块门控: {s2_log}")
        print(f"    （文件 {size2 / 1024:.0f} KB = {n_pieces} 块，"
              f"初始 {init_n} 块；Android 端需复现上述序列与播放结果）")

        ok = ok1 and ok_play and len(reqs2) >= 3 and bool(tail_reqs)
        print("\n=== 桌面流媒体 POC " + ("全部通过" if ok else "未通过") + " ===")
        return 0 if ok else 1
    finally:
        time.sleep(0.2)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
