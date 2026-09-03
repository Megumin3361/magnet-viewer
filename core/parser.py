"""bencode 编解码 + .torrent / magnet 链接解析（纯实现，零额外依赖）。"""
from __future__ import annotations

import hashlib
import os
import urllib.parse

from .models import ParseResult, TorrentFile, safe_rel_path


# ---------------- bencode ----------------

def bdecode(data: bytes):
    """解码 bencode 数据，返回 Python 对象（int / bytes / list / dict）。"""
    value, _ = _decode(data, 0)
    return value


def bencode(obj) -> bytes:
    """编码 Python 对象为 bencode（用于计算 info_hash）。"""
    if isinstance(obj, bool):
        raise TypeError("bool not supported")
    if isinstance(obj, int):
        return b"i" + str(obj).encode() + b"e"
    if isinstance(obj, bytes):
        return str(len(obj)).encode() + b":" + obj
    if isinstance(obj, str):
        b = obj.encode("utf-8")
        return str(len(b)).encode() + b":" + b
    if isinstance(obj, list):
        return b"l" + b"".join(bencode(x) for x in obj) + b"e"
    if isinstance(obj, dict):
        items = sorted((k, v) for k, v in obj.items())
        body = b"".join(bencode(k) + bencode(v) for k, v in items)
        return b"d" + body + b"e"
    raise TypeError(f"unsupported type: {type(obj)}")


def _decode(data: bytes, i: int):
    c = data[i:i + 1]
    if c == b"d":
        d, i = {}, i + 1
        while data[i:i + 1] != b"e":
            key, i = _decode(data, i)
            val, i = _decode(data, i)
            d[key] = val
        return d, i + 1
    if c == b"l":
        lst, i = [], i + 1
        while data[i:i + 1] != b"e":
            val, i = _decode(data, i)
            lst.append(val)
        return lst, i + 1
    if c == b"i":
        j = data.index(b"e", i)
        return int(data[i + 1:j]), j + 1
    j = data.index(b":", i)
    n = int(data[i:j])
    return data[j + 1:j + 1 + n], j + 1 + n


def _s(b) -> str:
    """bytes -> str（容忍非 UTF-8 文件名）。"""
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else str(b)


# BEP-52（BitTorrent v2）在 info 中引入的键
V2_INFO_KEYS = (b"meta version", b"file tree", b"piece layers")


def torrent_info_hash(info: dict) -> str:
    """按 BEP-3 / BEP-52 计算 info_hash。

    - v1（`meta version` 缺失或为 1）：**SHA-1**(bencoded info)，40 hex；
    - v2 / 混合（`meta version` >= 2）：**SHA-256**(bencoded info，含 v2 键)。

    返回统一截取为 40 hex —— 与 libtorrent `torrent_info::info_hash()`
    的返回值一致（libtorrent 对 v2 取 SHA-256 的前 20 字节），
    从而保证「本地种子」与「磁力链」两条入口解析同一资源得到相同标识。
    """
    try:
        meta_version = int(info.get(b"meta version", 1))
    except (TypeError, ValueError):
        meta_version = 1
    raw = bencode(info)
    if meta_version >= 2:
        return hashlib.sha256(raw).hexdigest()[:40]
    return hashlib.sha1(raw).hexdigest()


def is_pure_v2(info: dict) -> bool:
    """是否为本解析器不支持的「纯 v2」种子（只有 file tree，无 v1 的 files/length）。"""
    return b"file tree" in info and b"files" not in info and b"length" not in info


# ---------------- .torrent ----------------

def parse_torrent_file(path: str) -> ParseResult:
    """解析 .torrent 文件，返回 ParseResult（含每文件的 piece 区间）。"""
    with open(path, "rb") as fp:
        t = bdecode(fp.read())
    info = t[b"info"]
    name = safe_rel_path(_s(info.get(b"name", b"unknown")))
    piece_len = int(info.get(b"piece length", 16384))

    files: list[TorrentFile] = []
    offset = 0
    if is_pure_v2(info):
        # 纯 v2 只用 file tree 描述文件树，本解析器尚未实现；明确报错优于 KeyError
        raise ValueError(
            "这是纯 BitTorrent v2 种子（BEP-52，仅含 file tree），暂不支持。"
            "v1 与 v1+v2 混合种子可正常解析。")
    if b"files" in info:  # 多文件种子
        for i, f in enumerate(info[b"files"]):
            size = int(f[b"length"])
            inner = safe_rel_path(*(_s(seg) for seg in f[b"path"]))
            start = offset // piece_len
            end = (offset + size - 1) // piece_len if size > 0 else start
            files.append(TorrentFile(
                index=i, path=f"{name}/{inner}", size=size,
                offset=offset, start_piece=start, end_piece=end,
            ))
            offset += size
    else:  # 单文件种子
        # libtorrent 把单文件种子存为 save_path/name（不会再套一层目录），
        # 因此 path 必须是 name 本身；写成 name/name 会让磁盘路径整体错位，
        # 导致流服务 404、预览打不开。
        size = int(info[b"length"])
        files.append(TorrentFile(
            index=0, path=name, size=size,
            offset=0, start_piece=0,
            end_piece=(size - 1) // piece_len if size > 0 else 0,
        ))

    trackers = []
    for tier in t.get(b"announce-list", []):
        for tr in tier:
            trackers.append(_s(tr))
    if t.get(b"announce") and _s(t[b"announce"]) not in trackers:
        trackers.append(_s(t[b"announce"]))

    return ParseResult(
        info_hash=torrent_info_hash(info),
        name=name,
        total_size=sum(f.size for f in files if not f.is_pad),
        piece_size=piece_len,
        num_pieces=len(info.get(b"pieces", b"")) // 20,
        files=files,
        trackers=trackers,
        comment=_s(t.get(b"comment", "")),
        created_by=_s(t.get(b"created by", "")),
        source="torrent",
    )


# ---------------- magnet ----------------

def parse_magnet_uri(uri: str) -> dict:
    """解析磁力链，返回 {info_hash, display_name, trackers}。

    仅做轻量校验；info_hash 由 libtorrent 侧最终确认。
    """
    uri = uri.strip()
    if not uri.lower().startswith("magnet:?"):
        raise ValueError("不是有效的 magnet 链接")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
    xt = q.get("xt", [""])[0]
    if "urn:btih:" not in xt.lower():
        raise ValueError("magnet 链接缺少 btih 哈希（xt 字段）")
    info_hash = xt.split(":", 2)[-1]
    return {
        "info_hash": info_hash.lower(),
        "display_name": urllib.parse.unquote(q.get("dn", [""])[0]),
        "trackers": [urllib.parse.unquote(t[0]) for t in q.get("tr", [])],
    }


def is_torrent_path(source: str) -> bool:
    """判断输入是否应当作 .torrent 文件路径处理。"""
    if source.lower().endswith(".torrent"):
        return True
    return os.path.isfile(source) and not source.lower().startswith("magnet:")
