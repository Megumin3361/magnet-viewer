"""GUI 功能校验：主窗口实例化 + 拖放 + 输入历史自动补全 + 文件树展开。

无头运行：QT_QPA_PLATFORM=offscreen
用法：.\\.venv\\Scripts\\python.exe gui_feature_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (QModelIndex, QMimeData,  # noqa: E402
                            QStringListModel, QUrl)
from PySide6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import RECENT_LIMIT  # noqa: E402
from core.parser import parse_torrent_file  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

OK, FAIL = [], []


def check(cond, msg):
    (OK if cond else FAIL).append(msg)
    print(("  [OK] " if cond else "  [FAIL] ") + msg)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    tmp = tempfile.mkdtemp(prefix="mv_gui_")

    # ---------------------------------------------------------------- [1] 实例化
    print("\n[1] MainWindow 实例化")
    w = MainWindow()
    w.show()
    app.processEvents()
    check(w.windowTitle().startswith("磁力链"), "窗口标题正常")
    check(w.input is not None, "输入框存在")
    check(w.tabs.count() >= 2, f"页签数量 {w.tabs.count()} >= 2")

    # ---------------------------------------------------------------- [2] 拖放
    print("\n[2] 拖放支持")
    check(bool(w.acceptDrops()), "setAcceptDrops(True) 已启用")

    # 2a. 拖入磁力链文本 -> 应被接受
    md = QMimeData()
    md.setText("magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567")
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtCore import QPoint, Qt

    ev = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction | Qt.MoveAction, md,
                         Qt.LeftButton, Qt.NoModifier)
    w.dragEnterEvent(ev)
    check(ev.isAccepted(), "磁力链文本 dragEnter 被接受")

    # 2b. 拖入 .torrent 文件 -> 应被接受
    md2 = QMimeData()
    md2.setUrls([QUrl.fromLocalFile(os.path.join(tmp, "x.torrent"))])
    ev2 = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction | Qt.MoveAction, md2,
                          Qt.LeftButton, Qt.NoModifier)
    w.dragEnterEvent(ev2)
    check(ev2.isAccepted(), ".torrent 文件 dragEnter 被接受")

    # 2c. 拖入无关文本 -> 应被拒绝
    md3 = QMimeData()
    md3.setText("hello world")
    ev3 = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction | Qt.MoveAction, md3,
                          Qt.LeftButton, Qt.NoModifier)
    w.dragEnterEvent(ev3)
    check(not ev3.isAccepted(), "无关文本 dragEnter 被拒绝")

    # ---------------------------------------------------------------- [3] 历史
    print("\n[3] 输入历史 / 自动补全")
    check(hasattr(w, "_recent_model") and isinstance(w._recent_model,
                                                     QStringListModel),
          "_recent_model 已创建")
    check(w.input.completer() is not None, "输入框已挂载 QCompleter")
    check(w.input.completer().model() is w._recent_model,
          "completer 使用 _recent_model")

    sample = [f"magnet:?xt=urn:btih:{i:040d}" for i in range(3)]
    cur = list(w.cfg.recent())
    for s in sample:
        cur = w.cfg.push_recent(s)
    check(cur[:3] == sample[::-1], "新条目置顶（去重 + 逆序）")
    check(len(cur) <= RECENT_LIMIT, f"历史条数 {len(cur)} <= {RECENT_LIMIT}")

    for i in range(RECENT_LIMIT + 10):
        cur = w.cfg.push_recent(f"item-{i}")
    check(len(cur) == RECENT_LIMIT, f"历史上限生效：{len(cur)} == {RECENT_LIMIT}")
    check(w.cfg.recent() == cur, "QSettings 持久化读回一致")

    # ---------------------------------------------------------------- [4] 文件树
    print("\n[4] 文件树")
    torrents = [f for f in os.listdir(".") if f.endswith(".torrent")]
    if not torrents:
        import libtorrent as lt
        src = os.path.join(tmp, "payload")
        files = {
            "Season 01/ep1.mp4": os.urandom(200 * 1024),
            "Season 01/ep2.mp4": os.urandom(120 * 1024),
            "Season 02/ep1.mp4": os.urandom(90 * 1024),
            "Extras/pics/a.jpg": os.urandom(40 * 1024),
            "Extras/pics/b.png": os.urandom(16 * 1024),
            "readme.txt": b"hello magnet viewer",
        }
        for rel, data in files.items():
            p = os.path.join(src, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        fs = lt.file_storage()
        lt.add_files(fs, src)
        t = lt.create_torrent(fs, 16 * 1024)
        lt.set_piece_hashes(t, os.path.dirname(src))
        tpath = os.path.join(tmp, "gui_nested.torrent")
        with open(tpath, "wb") as f:
            f.write(lt.bencode(t.generate()))
        torrents = [tpath]
        print(f"  (已生成嵌套目录测试种子: {tpath})")
    if torrents:
        res = parse_torrent_file(torrents[0])
        w.tree.populate(res)
        model = w.tree.model()
        check(model.rowCount() > 0, f"模型根行数 {model.rowCount()} > 0")

        def walk(parent, depth=0, out=None):
            out = out if out is not None else []
            for r in range(model.rowCount(parent)):
                idx = model.index(r, 0, parent)
                out.append((depth, model.data(idx)))
                if model.hasChildren(idx):
                    print(f"        L{depth} 目录: {model.data(idx)}")
                    walk(idx, depth + 1, out)
            return out

        rows = walk(QModelIndex())
        n_files = len([d for _, d in rows if "." in d])
        check(n_files == len(res.view_files),
              f"叶子文件行 {n_files} == 可见文件 {len(res.view_files)}")
        check(any(d.endswith("Season 01") for _, d in rows),
              "二级目录（Season 01）在模型中可见")
        check(not any(".pad" in d for _, d in rows),
              "模型中没有 .pad 填充文件")
        check(max(d for d, _ in rows) >= 2,
              f"存在三级及以上层级（最深 depth={max(d for d, _ in rows)}）")

        # 目录行必须处于展开态
        collapsed = []

        def check_expanded(parent=QModelIndex()):
            for r in range(model.rowCount(parent)):
                idx = model.index(r, 0, parent)
                if model.hasChildren(idx) and not w.tree.isExpanded(idx):
                    collapsed.append(model.data(idx))
                check_expanded(idx)

        check_expanded()
        check(not collapsed, f"无折叠目录（折叠：{collapsed}）")

        # 本地 .torrent 的关键链路：磁盘绝对路径 -> TorrentFile 映射
        # （cache_dir 未注入时键会退化成相对路径，_pieces_map/_demand_range 全部查不到，
        #   预览随即退化成按完整静态文件服务，把未下载的稀疏零数据喂给播放器）
        w._on_metadata(res)
        keys = list(w._path_to_file)
        check(bool(keys), f"_path_to_file 非空（{len(keys)} 项）")
        check(all(os.path.isabs(k) for k in keys),
              "所有映射键均为绝对路径")
        sample = res.view_files[0]
        expect = os.path.normpath(os.path.join(
            w.cache_dir, *sample.path.split("/")))
        check(expect in w._path_to_file,
              f"可按绝对路径命中文件（{sample.name}）")

    # ---------------------------------------------------------------- [5] 清理
    print("\n[5] 收尾")
    w._stop_preview()
    w.close()
    app.processEvents()
    check(True, "close() 无异常")

    print(f"\n{'=' * 56}")
    print(f"通过 {len(OK)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print("  FAIL:", f)
    print("=" * 56)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
