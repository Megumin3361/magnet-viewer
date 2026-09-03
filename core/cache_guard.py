"""缓存目录守卫：防止「清理缓存」误删用户数据目录。

背景：缓存目录可由用户在设置面板自由配置（QSettings 持久化），
「立即清理/退出清理」会清空目录内容。若 cache_dir 指向磁盘根目录、
用户主目录或文档目录，一键清理即灾难（历史审查 P1-2）。

策略：
1. 高风险目录（盘符根、主目录及常见用户数据目录）直接拒绝作为缓存目录；
2. 受管缓存目录写入标记文件 `CACHE_MARKER`，清理前必须存在该标记
   —— 只有本程序声明过“这是我的缓存目录”的路径才允许被清空；
3. 清理内容必须走 `clear_cache_contents()` 的**保留名单**（downloads/、
   .tasks.json、.resume/ 是用户下载数据与任务持久化，绝不参与清理）——
   历史缺陷：设置对话框曾绕开保留名单二次清空整个目录（P0-1）。
"""
from __future__ import annotations

import os
import shutil

CACHE_MARKER = ".magnet_viewer_cache"

# 清理缓存时保留的条目（决策 D8/D9）：用户下载数据 + 任务持久化文件 + 受管标记
CLEANUP_KEEP: frozenset[str] = frozenset(
    {"downloads", ".tasks.json", ".resume", CACHE_MARKER})

_DATA_DIRS: frozenset[str] | None = None


def _user_data_dirs() -> frozenset[str]:
    """主目录 + 常见用户数据目录（规范化、小写化后缓存）。"""
    global _DATA_DIRS
    if _DATA_DIRS is None:
        home = os.path.expanduser("~")
        cands = [home,
                 os.path.join(home, "Documents"),
                 os.path.join(home, "Desktop"),
                 os.path.join(home, "Downloads"),
                 os.path.join(home, "Pictures"),
                 os.path.join(home, "Videos"),
                 os.path.join(home, "Music"),
                 os.path.join(home, "AppData")]
        _DATA_DIRS = frozenset(
            os.path.normcase(os.path.normpath(p)) for p in cands)
    return _DATA_DIRS


def is_risky_dir(path: str) -> bool:
    """盘符根 / 用户主目录与常见数据目录 → 禁止作为缓存目录。"""
    norm = os.path.normpath(os.path.abspath(path))
    drive, tail = os.path.splitdrive(norm)
    if tail in ("\\", "/", ""):          # 盘符根（C:\、D:/ 等）
        return True
    return os.path.normcase(norm) in _user_data_dirs()


def ensure_cache_dir(path: str) -> None:
    """校验并准备缓存目录（幂等）：拒绝高风险目录，写入受管标记文件。

    标记文件写入失败（只读介质等）不阻断使用——清理守卫会因缺标记而
    拒绝清理，属安全降级。
    """
    if is_risky_dir(path):
        raise ValueError(f"缓存目录不允许是磁盘根目录或用户数据目录：{path}")
    os.makedirs(path, exist_ok=True)
    marker = os.path.join(path, CACHE_MARKER)
    if not os.path.isfile(marker):
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write("magnet-viewer managed cache dir (safe to delete).\n")
        except OSError:
            pass


def guard_ok_for_cleanup(path: str, require_marker: bool = True) -> bool:
    """清理前守卫：非高风险目录，且（默认）含受管标记文件。"""
    if is_risky_dir(path):
        return False
    if require_marker:
        return os.path.isfile(os.path.join(path, CACHE_MARKER))
    return True


def clear_cache_contents(cache_dir: str, keep: frozenset | None = None) -> int:
    """清空缓存目录内容，但保留名单之外的条目一律删除（唯一清理入口）。

    **保留名单**（默认 CLEANUP_KEEP）：downloads/（用户下载数据）、
    .tasks.json（任务清单）、.resume/（fastresume 续传数据）、受管标记。

    调用方须先经 guard_ok_for_cleanup() 守卫（本函数不重复校验，便于
    设置对话框对编辑框当前值先校验再清理）。返回删除的条目数，
    目录不存在返回 -1。失败条目静默跳过（与既有清理语义一致）。
    """
    keep = CLEANUP_KEEP if keep is None else frozenset(keep)
    if not os.path.isdir(cache_dir):
        return -1
    removed = 0
    for name in os.listdir(cache_dir):
        if name in keep:
            continue
        p = os.path.join(cache_dir, name)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
            removed += 1
        except OSError:
            pass
    return removed