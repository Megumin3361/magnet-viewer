"""冒烟测试（无 GUI）：
1) 用 libtorrent 生成一个含图片和视频后缀的测试种子；
2) 用自研 parser 解析，校验文件树/大小/piece 区间/info_hash；
3) 启动本地流服务并验证 Range 请求（含分块级可用性 / 尾部 moov 窗口）；
4) 校验 core/ui 模块可完整导入。
"""
import hashlib
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import libtorrent as lt  # noqa: E402

from core.config import lt_proxy_settings  # noqa: E402
from core.fetcher import SessionManager  # noqa: E402
from core.models import (PieceMap, TorrentFile, contiguous_bytes,  # noqa: E402
                         file_disk_path, human_size, range_available,
                         safe_rel_path)
from core.parser import bencode, parse_torrent_file, torrent_info_hash  # noqa: E402
from core.stream_server import StreamServer, _is_within  # noqa: E402
from core.scheduler import PreviewScheduler, tail_piece_window  # noqa: E402


def make_test_torrent(dirpath: str) -> str:
    src = os.path.join(dirpath, "payload")
    os.makedirs(os.path.join(src, "pics"), exist_ok=True)
    files = {
        "video/demo.mp4": os.urandom(300 * 1024),
        "pics/a.jpg": os.urandom(48 * 1024),
        "pics/b.png": os.urandom(16 * 1024),
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
    out = os.path.join(dirpath, "test.torrent")
    with open(out, "wb") as f:
        f.write(lt.bencode(t.generate()))
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="mv_smoke_")
    torrent = make_test_torrent(tmp)
    print("[1] 测试种子已生成:", torrent)

    r = parse_torrent_file(torrent)
    assert r.source == "torrent" and len(r.info_hash) == 40
    # libtorrent 生成种子会插入 BEP-47 .pad 对齐填充文件：索引必须与 libtorrent 一致
    # （预览时 prioritize_files / file_progress 依赖该索引），但展示与体积统计需过滤
    assert len(r.view_files) == 4 and len(r.files) >= 4, \
        f"文件数错误: 全量 {len(r.files)} / 可见 {len(r.view_files)}"
    pads = [f for f in r.files if f.is_pad]
    assert len(pads) == len(r.files) - len(r.view_files)
    sizes = {f.name: f.size for f in r.view_files}
    assert sizes["demo.mp4"] == 300 * 1024
    assert sum(f.size for f in r.view_files) == r.total_size
    for f in r.files:
        assert f.start_piece <= f.end_piece
    vid = next(f for f in r.view_files if f.is_video)
    imgs = [f for f in r.view_files if f.is_image]
    assert vid.is_video and len(imgs) == 2 and all(i.is_image for i in imgs)
    print(f"[2] parser 通过：{len(r.view_files)} 可见文件（含 {len(r.files) - len(r.view_files)} "
          f"个 .pad 填充）, 共 {human_size(r.total_size)}, "
          f"piece={r.piece_size}B, ih={r.info_hash[:16]}…")

    # ---------------- [2a] 本地 .torrent 必须注入 cache_dir ----------------
    # 主窗口用它建「磁盘绝对路径 -> TorrentFile」映射；若为空则键退化成相对路径，
    # _pieces_map 与 _demand_range 全部查不到 → 预览退化为按完整静态文件服务，
    # 把未下载的稀疏零数据喂给播放器（即此前修掉的 moov 问题会原样复发）。
    mgr_i = SessionManager(os.path.join(tmp, "cacheI"))
    mgr_i.start()
    got_i: list = []
    mgr_i.on_metadata = got_i.append
    mgr_i.resolve(torrent)
    for _ in range(100):  # 本地种子无需网络，元数据应立即回调
        if got_i:
            break
        time.sleep(0.05)
    mgr_i.shutdown()
    assert got_i, "本地 .torrent 未触发元数据回调"
    ri = got_i[0]
    assert ri.cache_dir == os.path.abspath(os.path.join(tmp, "cacheI")), \
        f"cache_dir 未注入或不一致: {ri.cache_dir!r}"
    disk_i = file_disk_path(ri.cache_dir, ri.files[0])
    assert os.path.isabs(disk_i), f"磁盘路径应为绝对路径: {disk_i}"
    assert _is_within(os.path.normpath(ri.cache_dir), os.path.normpath(disk_i))
    print(f"[2a] 本地 .torrent 注入 cache_dir 通过：{ri.cache_dir}")

    # ---------------- [2c] BEP-52 种子版本 ----------------
    # libtorrent 2.x 的 create_torrent() 默认产出 v1+v2 混合种子（meta version=2）。
    # BEP-52 规定 v2/混合种子的 info_hash 是 SHA-256，而非 v1 的 SHA-1。
    def _gen_torrent(tdir: str, flag: int | None) -> tuple[str, dict]:
        src = os.path.join(tdir, "src")
        os.makedirs(os.path.join(src, "sub"), exist_ok=True)
        for rel in ("a.mkv", "sub/b.txt"):
            p = os.path.join(src, *rel.split("/"))
            with open(p, "wb") as f:
                f.write(os.urandom(64 * 1024))
        fs = lt.file_storage()
        lt.add_files(fs, src)
        ct = lt.create_torrent(fs, flags=flag) if flag is not None \
            else lt.create_torrent(fs)
        lt.set_piece_hashes(ct, os.path.dirname(src))
        gen = ct.generate()
        out = os.path.join(tdir, "v.torrent")
        with open(out, "wb") as f:
            f.write(lt.bencode(gen))
        return out, gen[b"info"]

    # 混合种子（默认）：必须用 SHA-256，且要与 libtorrent info_hash() 一致
    d1 = os.path.join(tmp, "v2hybrid")
    os.makedirs(d1)
    tp1, info1 = _gen_torrent(d1, None)
    assert int(info1.get(b"meta version", 1)) == 2, "默认应为混合(v2)种子"
    want = str(lt.torrent_info(tp1).info_hash())
    got_h = torrent_info_hash(info1)
    assert got_h == want, f"混合种子 info_hash 错误：{got_h} != {want}"
    assert parse_torrent_file(tp1).info_hash == want, "parser 未使用 SHA-256"
    assert len(got_h) == 40, f"应统一为 40 hex（与磁力链入口一致），实际 {len(got_h)}"
    print(f"[2c] 混合 v2 种子 info_hash 通过（SHA-256 前 20 字节）：{got_h[:16]}…")

    # 纯 v1 种子：仍走 SHA-1
    d2 = os.path.join(tmp, "v1only")
    os.makedirs(d2)
    tp2, info2 = _gen_torrent(d2, lt.create_torrent.v1_only)
    assert b"meta version" not in info2, "v1_only 不应含 meta version"
    want1 = str(lt.torrent_info(tp2).info_hash())
    assert torrent_info_hash(info2) == want1, "v1 种子应走 SHA-1"
    assert parse_torrent_file(tp2).info_hash == want1
    print(f"[2c] 纯 v1 种子 info_hash 通过（SHA-1）：{want1[:16]}…")

    # 纯 v2 种子：本解析器暂不支持，必须给出明确报错而非 KeyError
    d3 = os.path.join(tmp, "v2only")
    os.makedirs(d3)
    tp3, info3 = _gen_torrent(d3, lt.create_torrent.v2_only)
    assert b"file tree" in info3 and b"files" not in info3, "应为纯 v2 结构"
    try:
        parse_torrent_file(tp3)
        raise AssertionError("纯 v2 种子应被拒绝")
    except ValueError as e:
        assert "v2" in str(e) and "file tree" in str(e), \
            f"报错信息应说明原因，实际：{e}"
    print("[2c] 纯 v2 种子给出明确报错（而非 KeyError）")

    # ---------------- [2b] 路径穿越防护 ----------------
    # 恶意种子可声明 path: ["..","..","Windows","win.ini"] 或绝对路径，
    # 原样拼接会让预览 / 流服务逃出缓存目录。
    cases = {
        ("..", "..", "..", "Windows", "win.ini"): "Windows/win.ini",
        ("a", "..", "b"): "a/b",
        (".", "", "c.mp4"): "c.mp4",
        ("C:\\evil", "x.mp4"): "evil/x.mp4",     # 盘符段被丢弃（\ 先归一为 /）
        ("/abs/olute.mp4",): "abs/olute.mp4",    # 根前缀被丢弃
        ("sub\\win\\back.mp4",): "sub/win/back.mp4",
        ("we:ird|na<>me?.mp4",): "we_ird_na__me_.mp4",  # Windows 非法字符替换
        ("..",): "unnamed",
    }
    for segs, expect in cases.items():
        got = safe_rel_path(*segs)
        assert got == expect, f"safe_rel_path{segs} -> {got!r}，期望 {expect!r}"
    assert safe_rel_path() == "unnamed"

    def _torrent_with_path(tdir: str, segs: list[bytes]) -> str:
        """构造 name/path 含穿越段的恶意种子。"""
        src = os.path.join(tdir, "src")
        os.makedirs(os.path.dirname(os.path.join(src, "ok.txt")), exist_ok=True)
        with open(os.path.join(src, "ok.txt"), "wb") as f:
            f.write(b"payload")
        info = {
            b"name": b"../../escaped",
            b"piece length": 16384,
            b"pieces": hashlib.sha1(b"payload").digest(),
            b"files": [{b"length": 7, b"path": segs}],
        }
        out = os.path.join(tdir, "evil.torrent")
        with open(out, "wb") as f:
            f.write(bencode({b"info": info}))
        return out

    evil = _torrent_with_path(tmp, [b"..", b"..", b"..", b"Windows", b"win.ini"])
    rv = parse_torrent_file(evil)
    f0 = rv.files[0]
    assert ".." not in f0.path.split("/") and not os.path.isabs(f0.path), \
        f"恶意路径未被净化: {f0.path}"
    cache0 = os.path.join(tmp, "cache0")
    escaped = file_disk_path(cache0, f0)
    assert _is_within(os.path.normpath(cache0), os.path.normpath(escaped)), \
        f"file_disk_path 逃出缓存目录: {escaped}"
    print(f"[2b] 路径穿越防护通过：恶意路径 {f0.path!r} 被约束在缓存目录内")

    # 流服务：直接请求穿越路径必须 404
    cache_t = os.path.join(tmp, "cacheT")
    os.makedirs(cache_t, exist_ok=True)
    with open(os.path.join(cache_t, "in.mp4"), "wb") as f:
        f.write(b"x" * 4096)
    # 同前缀兄弟目录（startswith 校验的绕过目标）
    sib = os.path.join(tmp, "cacheT_evil")
    os.makedirs(sib, exist_ok=True)
    with open(os.path.join(sib, "secret.mp4"), "wb") as f:
        f.write(b"SECRET")
    srv0 = StreamServer(cache_t)
    srv0.start()
    base = srv0.url_for("in.mp4").rsplit("/", 1)[0]
    for attack in ("../../../evil.torrent",
                   "%2e%2e%2f%2e%2e%2f%2e%2e%2fWindows%2fwin.ini",
                   "/../cacheT_evil/secret.mp4",
                   f"/{os.path.basename(tmp)}/cacheT_evil/secret.mp4"):
        try:
            urllib.request.urlopen(f"{base}/{attack}", timeout=5)
            raise AssertionError(f"穿越请求未被拒绝: {attack}")
        except urllib.error.HTTPError as e:
            assert e.code == 404, f"{attack} -> {e.code}，期望 404"
    with urllib.request.urlopen(f"{base}/in.mp4", timeout=5) as resp:
        assert resp.read() == b"x" * 4096, "合法路径应可正常读取"
    srv0.shutdown()
    print("[2b] 流服务目录穿越（含 %2e 编码与同前缀兄弟目录）全部返回 404")

    # 流服务 Range 测试
    cache = os.path.join(tmp, "cache")
    os.makedirs(cache, exist_ok=True)
    rel = vid.path
    disk = file_disk_path(cache, vid)
    os.makedirs(os.path.dirname(disk), exist_ok=True)
    with open(disk, "wb") as f:
        f.write(os.urandom(300 * 1024))

    srv = StreamServer(cache)
    srv.start()
    url = srv.url_for(rel)
    req = urllib.request.Request(url, headers={"Range": "bytes=100-199"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = resp.read()
        assert resp.status == 206 and len(body) == 100, (resp.status, len(body))
    with urllib.request.urlopen(url, timeout=5) as resp:
        assert resp.status == 200 and len(resp.read()) == 300 * 1024
    srv.shutdown()
    print("[3] 流服务通过：Range(206) 与全量(200) 均正确, url =", url[:48] + "…")

    # 流服务「已下载前缀钳制」测试（边下边播核心：不暴露稀疏零数据）
    state = {"avail": 100 * 1024}
    srv2 = StreamServer(cache, avail_cb=lambda p: state["avail"], wait_timeout=0)
    srv2.start()
    url2 = srv2.url_for(rel)
    with urllib.request.urlopen(url2, timeout=5) as resp:      # 全量 → 钳制到前缀
        body = resp.read()
        assert resp.status == 200 and len(body) == 100 * 1024, (resp.status, len(body))
    req = urllib.request.Request(url2, headers={"Range": "bytes=0-99"})
    with urllib.request.urlopen(req, timeout=5) as resp:       # 前缀内 Range → 206
        assert resp.status == 206 and len(resp.read()) == 100
    try:                                                        # 超出前缀 → 416
        urllib.request.urlopen(urllib.request.Request(
            url2, headers={"Range": f"bytes={150*1024}-"}), timeout=5)
        raise AssertionError("应当返回 416")
    except urllib.error.HTTPError as e:
        assert e.code == 416, e.code
    state["avail"] = 0
    try:                                                        # 零前缀 → 503
        urllib.request.urlopen(url2, timeout=5)
        raise AssertionError("应当返回 503")
    except urllib.error.HTTPError as e:
        assert e.code == 503, e.code
    srv2.shutdown()
    print("[3b] 前缀钳制通过：200 钳制 / 206 前缀内 / 416 超界 / 503 未就绪")

    # 中文 / 特殊字符文件名端到端往返（真实种子极常见）
    cache_cn = os.path.join(tmp, "cacheCN")
    os.makedirs(os.path.join(cache_cn, "剧集 第一季"), exist_ok=True)
    weird = "剧集 第一季/[字幕组] 第01集 测试#1&x+100%.mp4"
    payload = os.urandom(64 * 1024)
    with open(os.path.join(cache_cn, *weird.split("/")), "wb") as f:
        f.write(payload)
    srv_cn = StreamServer(cache_cn)
    srv_cn.start()
    url_cn = srv_cn.url_for(weird)
    assert "%" in url_cn and " " not in url_cn, f"URL 未正确编码: {url_cn}"
    with urllib.request.urlopen(url_cn, timeout=5) as resp:
        body = resp.read()
    assert body == payload, f"内容不一致：{len(body)} != {len(payload)}"
    # 特殊文件名同样受穿越防护约束（不能借编码绕过）
    try:
        urllib.request.urlopen(srv_cn.url_for("../evil.torrent"), timeout=5)
        raise AssertionError("编码后的穿越请求未被拒绝")
    except urllib.error.HTTPError as e:
        assert e.code == 404, e.code
    srv_cn.shutdown()
    print("[3b2] 中文/特殊字符文件名往返通过：编码、读取一致，且仍受穿越防护约束")

    # 尾部索引窗口（moov 优先）纯逻辑测试
    mp4 = TorrentFile(0, "root/demo.mp4", 100 * 1024 * 1024, 0, 0, 6399)
    mkv = TorrentFile(1, "root/demo.mkv", 100 * 1024 * 1024, 0, 0, 6399)
    win = tail_piece_window(mp4, 16 * 1024)
    assert win and win == list(range(6399 - len(win) + 1, 6400)), win[:3]
    assert win[-1] == 6399 and len(win) <= 128  # 尾部窗口必含末块、有上限
    assert tail_piece_window(mkv, 16 * 1024) == []  # 非 MP4 家族不补拉
    assert tail_piece_window(mp4, 0) == []
    tiny = TorrentFile(2, "root/a.mp4", 300 * 1024, 0, 0, 18)
    assert tail_piece_window(tiny, 16 * 1024) == list(range(19))  # 小文件=整文件
    print(f"[3c] 尾部索引窗口通过：MP4 末块 {win[-1]} 窗口 {len(win)} 块 / MKV 不补拉")

    # 分块映射：连续前缀 + 任意区间可用性（moov 在尾部的判定基础）
    pl, size = 16 * 1024, 300 * 1024
    have = {0, 1, 2}   # 头部 3 块 + 尾部 2 块（中间缺失模拟稀疏空洞）
    have |= {17, 18}
    pm = PieceMap(pl, 0, 0, (size - 1) // pl, size, have.__contains__)
    assert contiguous_bytes(pm) == 3 * pl
    assert range_available(pm, 0, 100) and range_available(pm, 17 * pl, size)
    assert not range_available(pm, 100, 17 * pl)  # 跨空洞不可读
    assert range_available(pm, 20, 10)  # 空区间恒真
    print("[3d] 分块映射通过：头部连续 48KB，尾部窗口可精确服务")

    # 流服务「分块级可用性」测试：头部+尾部已下载、中间空洞 → 只服务就绪区间
    # wait_timeout=0：保留"未就绪立即 416"的既有行为（本组用例不测等待）
    srv3 = StreamServer(cache, pieces_cb=lambda path: pm if path == disk else None,
                        wait_timeout=0)
    srv3.start()
    url3 = srv3.url_for(rel)
    with urllib.request.urlopen(url3, timeout=5) as resp:      # 无 Range → 连续前缀
        body = resp.read()
        assert resp.status == 200 and len(body) == 3 * pl, (resp.status, len(body))
    req = urllib.request.Request(url3, headers={"Range": "bytes=0-99"})
    with urllib.request.urlopen(req, timeout=5) as resp:       # 前缀内 → 206
        assert resp.status == 206 and len(resp.read()) == 100
    req = urllib.request.Request(url3, headers={"Range": "bytes=200000-"})
    try:                                                        # 空洞中段 → 416
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("应当返回 416")
    except urllib.error.HTTPError as e:
        assert e.code == 416, e.code
    req = urllib.request.Request(url3, headers={"Range": "bytes=-100"})
    with urllib.request.urlopen(req, timeout=5) as resp:       # 后缀 → 逻辑大小相对
        body = resp.read()
        assert resp.status == 206 and len(body) == 100, (resp.status, len(body))
        assert body == open(disk, "rb").read()[-100:]
    try:                                                        # 空洞段 → 416 + 逻辑总长
        urllib.request.urlopen(urllib.request.Request(
            url3, headers={"Range": "bytes=49152-200000"}), timeout=5)
        raise AssertionError("应当返回 416")
    except urllib.error.HTTPError as e:
        assert e.code == 416 and e.headers.get("Content-Range") == f"bytes */{size}", \
            (e.code, e.headers.get("Content-Range"))
    srv3.shutdown()
    print("[3e] 分块级流服务通过：206 就绪区间 / 416 空洞 / 后缀相对逻辑大小")

    # 「点播 + 等待」：未就绪区间应先向调度器点播、等数据到达后服务，而非立刻 416
    # （这是 MP4 尾部 moov 探测成功的关键：直接 416 会被 FFmpeg 判为 moov not found）
    have2 = {0, 1, 2}
    demanded = []
    pm2 = PieceMap(pl, 0, 0, (size - 1) // pl, size, lambda p: p in have2)

    def demand(path, start, end_excl):
        demanded.append((start, end_excl))

        def fill():
            time.sleep(0.3)
            have2.update(range(0, (size - 1) // pl + 1))  # 模拟数据陆续到达
        threading.Thread(target=fill, daemon=True).start()

    srv4 = StreamServer(cache, pieces_cb=lambda path: pm2 if path == disk else None,
                        demand_cb=demand, wait_timeout=5.0)
    srv4.start()
    url4 = srv4.url_for(rel)
    req = urllib.request.Request(url4, headers={"Range": f"bytes={10 * pl}-{11 * pl - 1}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read()
        assert resp.status == 206 and len(body) == pl, (resp.status, len(body))
    assert demanded and demanded[0][0] == 10 * pl, f"未触发点播: {demanded}"
    srv4.shutdown()

    # 等待超时后仍不可满足 → 退化为 416（不挂死连接）
    srv5 = StreamServer(cache, pieces_cb=lambda path: pm if path == disk else None,
                        demand_cb=lambda p, s, e: None, wait_timeout=0.5)
    srv5.start()
    req = urllib.request.Request(srv5.url_for(rel), headers={"Range": "bytes=200000-"})
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("应当返回 416")
    except urllib.error.HTTPError as e:
        assert e.code == 416, e.code
    srv5.shutdown()
    print("[3f] 点播+等待通过：未就绪区间先点播再等待，超时才退化为 416")

    # 配置映射：应用代理配置 → libtorrent 设置键值（纯函数）
    from core.config import lt_proxy_settings
    assert lt_proxy_settings(None) == {"proxy_type": 0,
                                       "proxy_peer_connections": False}
    m = lt_proxy_settings({"type": "socks5", "host": "127.0.0.1", "port": 1080,
                           "peer": True})
    assert m["proxy_type"] == 2 and m["proxy_hostname"] == "127.0.0.1"
    assert m["proxy_port"] == 1080 and m["proxy_peer_connections"] is True
    assert m["proxy_tracker_connections"] is True
    mp = lt_proxy_settings({"type": "http", "host": "p", "port": 8080,
                            "user": "u", "pass": "x"})
    assert mp["proxy_type"] == 5 and mp["proxy_username"] == "u"   # http + 账号 → http_pw
    assert lt_proxy_settings({"type": "socks5", "host": ""})["proxy_type"] == 0  # 空主机回落直连
    print("[3g] 代理配置映射通过：直连/socks5/http_pw/空主机回落")

    # 会话启动参数：代理 + 自定义元数据超时
    mgr = SessionManager(os.path.join(tmp, "px_cache"), listen_port=6893)
    mgr.start(proxy={"type": "none"}, metadata_timeout=45)
    assert mgr.metadata_timeout == 45
    mgr.shutdown()
    print("[3h] 会话启动参数通过：metadata_timeout=45 生效")

    # 模块导入完整性
    from ui.file_tree import FileTreeWidget  # noqa: F401
    from ui.gallery import GalleryWidget  # noqa: F401
    from ui.main_window import MainWindow  # noqa: F401
    from ui.preview_pane import PreviewPane  # noqa: F401
    from ui.preview_player import VideoPreviewWidget  # noqa: F401
    from ui.status_panel import StatusPanel  # noqa: F401
    from core.scheduler import PreviewScheduler  # noqa: F401
    print("[4] 全部模块导入通过")

    print("\n=== 冒烟测试全部通过 ===")


if __name__ == "__main__":
    main()
