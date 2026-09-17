"""symlink/junction 逃逸防护验证（audit P2-3）—— 确定性，无需 libtorrent。

背景：越界判定此前是纯词法（normpath+commonpath）。NTFS junction 在 OS 层
把 <root>/link 解析到 root 之外，词法过关 → 流服务可读 root 外任意文件、
「缓存目录=用户数据目录的链接」可骗过清理守卫。models.is_within_root 现做
词法+realpath 双重校验；cache_guard.ensure_cache_dir 拒绝解析后落在高风险
目录的链接。

Windows 下 symlink 需要特权，但 junction（mklink /J）无需管理员即可创建——
本测试用 junction。创建失败（权限/非 NTFS）时显式 SKIP（退出码 2，绝不假绿）。

用法：python symlink_guard_test.py
退出码：0=PASS / 1=FAIL / 2=SKIP（环境不支持 junction）
"""
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.cache_guard import ensure_cache_dir  # noqa: E402
from core.models import is_within_root  # noqa: E402
from core.stream_server import StreamServer, _is_within  # noqa: E402

OK: list[str] = []
FAIL: list[str] = []


def check(cond, msg):
    (OK if cond else FAIL).append(msg)
    print(("  [OK] " if cond else "  [FAIL] ") + msg)


def make_junction(link: str, target: str) -> bool:
    """创建 NTFS junction（无需管理员）。mklink 输出为 OEM 代码页，
    text=True 会 UnicodeDecodeError——按 bytes 处理。"""
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                       capture_output=True)
    return r.returncode == 0 and os.path.isdir(link)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="mv_symlink_")
    try:
        root = os.path.join(tmp, "cache")
        outside = os.path.join(tmp, "outside")
        os.makedirs(os.path.join(root, "sub"))
        os.makedirs(outside)
        with open(os.path.join(outside, "secret.txt"), "wb") as f:
            f.write(b"TOP-SECRET-OUTSIDE")
        payload = b"legit-inside-cache" * 64
        with open(os.path.join(root, "movie.bin"), "wb") as f:
            f.write(payload)
        link = os.path.join(root, "link")
        if not make_junction(link, outside):
            print("  [SKIP] mklink /J 失败（权限/文件系统不支持），无法验证 P2-3")
            print("symlink_guard_test：SKIP（环境不支持 junction）")
            return 2
        check(os.path.realpath(link) == os.path.realpath(outside),
              "junction 已建立且 realpath 解析到根外（测试前提成立）")

        # --- [1] 词法+realpath 双重校验的纯函数断言 ---
        print("\n[1] models.is_within_root：链接逃逸")
        esc = os.path.join(link, "secret.txt")
        # 词法上 esc 在 root 内（旧实现会放行）——先确认这一点，防止测试环境漂移
        lexical_ok = (os.path.commonpath([os.path.normcase(os.path.normpath(root)),
                                          os.path.normcase(os.path.normpath(esc))])
                      == os.path.normcase(os.path.normpath(root)))
        check(lexical_ok, "前提：越界路径在词法上位于 root 内（旧实现盲区存在）")
        check(not is_within_root(root, esc),
              "junction 逃逸路径被 realpath 双重校验拒绝")
        check(is_within_root(root, os.path.join(root, "movie.bin")),
              "根内真实文件不误伤")
        check(is_within_root(root, os.path.join(root, "sub", "deep", "x")),
              "根内不存在的深层路径（最长存在前缀仍在根内）不误伤")
        check(not is_within_root(root, os.path.join(root, "..", "outside", "secret.txt")),
              "经典 .. 穿越仍被拒绝")

        # --- [2] 流服务端到端：经 junction 请求根外文件 → 404，根内文件仍 206 ---
        print("\n[2] StreamServer：经 junction 的供给必须 404")
        srv = StreamServer(root)
        srv.start()
        try:
            try:
                urllib.request.urlopen(srv.url_for("link/secret.txt"), timeout=5)
                check(False, "经 junction 读到根外文件 = 逃逸成功（缺陷）")
            except urllib.error.HTTPError as e:
                check(e.code in (403, 404),
                      f"经 junction 请求 secret → {e.code}（拒绝）")
            req = urllib.request.Request(
                srv.url_for("movie.bin"), headers={"Range": "bytes=0-99"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read()
                check(resp.status == 206 and body == payload[:100],
                      "根内合法文件仍正常 206（不过度拦截）")
        finally:
            srv.shutdown()

        # --- [3] cache_guard：缓存目录本身是指向用户数据目录的链接 → 拒绝 ---
        print("\n[3] ensure_cache_dir：拒绝指向高风险目录的链接")
        docs = os.path.join(tmp, "fake_documents")
        os.makedirs(docs)
        risky_link = os.path.join(tmp, "cache_via_link")
        ok = make_junction(risky_link, docs)
        if not ok:
            print("  [SKIP] 第二个 junction 创建失败")
        else:
            # 词法上 risky_link 在 tmp 内不是高风险目录；只有把「高风险」
            # 定义给 realpath 结果才暴露。is_risky_dir 按 basename 判的是
            # 已知用户目录清单，Documents 恰在其中（home/Documents）——
            # 为确定性，改为链接到真实 ~/Documents 的判定路径名：
            try:
                ensure_cache_dir(risky_link)
                # 未抛错也接受：目标是临时假 Documents，不在 _user_data_dirs
                # 清单（该清单比对的是 ~/Documents 的真实路径）。
                check(True, "指向非清单目录的链接不阻断（清单精确匹配，防误伤）")
            except ValueError as e:
                check(False, f"指向临时目录的链接被误拒：{e}")
        # 真实高风险目标：~ 直接是用户主目录（在清单内）
        home = os.path.expanduser("~")
        home_link = os.path.join(tmp, "cache_via_home_link")
        if make_junction(home_link, home):
            try:
                ensure_cache_dir(home_link)
                check(False, "缓存目录=指向主目录的 junction 应被拒绝（缺陷）")
            except ValueError:
                check(True, "指向主目录的 junction 被拒绝（P2-3 核心断言）")
        else:
            print("  [SKIP] 主目录 junction 创建失败")

        # --- [4] 回归：无链接环境的既有语义不变 ---
        print("\n[4] 无链接场景语义回归")
        check(_is_within(root, os.path.join(root, "sub")), "模块契约名 _is_within 仍可用")
        check(not _is_within(root, os.path.join(outside, "secret.txt")),
              "根外绝对路径仍 404 语义（False）")
        sib_root = tmp + "_evilsib"
        os.makedirs(sib_root, exist_ok=True)
        check(not is_within_root(root, os.path.join(sib_root, "x")),
              "同前缀兄弟目录仍被拒绝（历史 P3-1 边界）")
        shutil.rmtree(sib_root, ignore_errors=True)
    finally:
        # junction 必须先 unlink 再删目标侧？rmtree 对 junction 只删链接点
        # （Python 3.8+ os.path.islink 对 reparse point 返回 True），安全。
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nsymlink_guard_test：OK {len(OK)} 项，FAIL {len(FAIL)} 项")
    if FAIL:
        for m in FAIL:
            print("  X " + m)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
