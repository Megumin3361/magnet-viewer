"""下载续传数据（fastresume）路径约定与编解码（纯函数模块）。

约定（plan/t2 §2 B3）：``<cache_dir>/.resume/<ih>.fastresume``，
内容为 libtorrent resume data 的 bencode（顶层 dict、key 全为 bytes）。
编解码复用 ``core.parser`` 的自研 bencode（零 libtorrent 会话依赖、带 DoS
防御、确定性）——不用 ``lt.bencode``：2.1.x python 绑定对 int 值编码会触发
DeprecationWarning、且不支持 int 键（resume data 的量/位图恰是 int/bytes 混合），
自研编解码已在 smoke_test [2d] 与 ``lt.bdecode`` 做交叉互通验证。

- 解码失败 / 顶层非 dict = 数据损坏 → 返回 None，调用方静默重建
  （损坏 fastresume 绝不阻断启动）；
- info_hash 文件名强制 40/64 hex（复用 taskstore.normalize_info_hash），
  防路径注入（ih 可能来自不可信来源的拼接路径）；
- 写盘复用 taskstore.atomic_write_bytes（原子替换，不产生半截文件）。
"""
from __future__ import annotations

import os

from .parser import bdecode as _bdecode
from .parser import bencode as _bencode
from .taskstore import atomic_write_bytes, normalize_info_hash

RESUME_SUBDIR = ".resume"


def resume_dir(cache_dir: str) -> str:
    """fastresume 目录：``<cache_dir>/.resume``（由调用方保证 cache_dir 合法）。"""
    return os.path.join(cache_dir, RESUME_SUBDIR)


def resume_path(cache_dir: str, info_hash: str) -> str:
    """单个任务的 fastresume 文件路径：``<cache_dir>/.resume/<ih>.fastresume``。

    info_hash 非法（非 40/64 hex）抛 ValueError——文件名由外部拼入，必须防穿越。
    """
    ih = normalize_info_hash(info_hash, raise_invalid=True)
    return os.path.join(resume_dir(cache_dir), f"{ih}.fastresume")


def encode_resume(data: dict) -> bytes:
    """把 resume data dict（bytes key）编码为 bencode 字节。

    键必须是 bytes/str（libtorrent resume 格式规范），不合格抛 ValueError。
    """
    try:
        return _bencode(data)
    except Exception as e:
        raise ValueError(f"fastresume 编码失败：{e}") from e


def decode_resume(data: bytes) -> dict | None:
    """解码 fastresume 字节 → dict；损坏（含顶层非 dict）返回 None。"""
    try:
        out = _bdecode(data)
    except Exception:
        return None
    return out if isinstance(out, dict) else None


def write_resume(cache_dir: str, info_hash: str, data: dict) -> str:
    """把 resume data 原子写入磁盘（时机由调用方决定：暂停/退出/60s 周期脏）。

    返回写出的文件路径。
    """
    return atomic_write_bytes(resume_path(cache_dir, info_hash),
                              encode_resume(data))