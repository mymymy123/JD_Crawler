"""恢复工作区代码到「最初」状态。

恢复（覆盖）：
  - login.py  -> _recovered/original_login.py   (146 行，原始版)
  - search.py -> _recovered/original_search.py  ( 83 行，最初版)

归档（移入 _recovered/created_by_agent/，不删除）：
  本次会话新建的诊断/验证/临时脚本与测试输出。
  _recovered/ 本身会被保留，不参与移动。
"""
import ast
import io
import shutil
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = Path(__file__).resolve().parent
REC = BASE / "_recovered"
ARCHIVE = REC / "created_by_agent"
ARCHIVE.mkdir(parents=True, exist_ok=True)

# ---------- 1. 恢复代码文件 ----------
print("=" * 68)
print("步骤1：恢复代码文件")
plan = [
    ("login.py", REC / "original_login.py", 146),
    ("search.py", REC / "original_search.py", 83),
]
for target_name, src, expect_lines in plan:
    target = BASE / target_name
    if not src.exists():
        print(f"  [!] 缺少原始文件 {src.name}，跳过")
        continue
    text = src.read_text(encoding="utf-8")
    n = len(text.splitlines())
    assert n == expect_lines, f"{src.name} 行数 {n} != 期望 {expect_lines}"
    ast.parse(text)                                    # 语法自检
    if target.exists():                                # 先留一份现状快照
        snap = REC / f"before_restore_{target_name}"
        shutil.copy2(target, snap)
        print(f"  已快照现状 -> {snap.name}")
    shutil.copy2(src, target)
    back = target.read_text(encoding="utf-8")
    same = back == text
    print(f"  恢复 {target_name:12s} <- {src.name:22s} "
          f"{n:4d} 行 / {len(back.encode('utf-8')):5d} 字节  一致={same}")

# ---------- 2. 归档本次会话新建的文件 ----------
print("=" * 68)
print("步骤2：归档本次会话新建的脚本与输出")
created = [
    "diag_dom.py", "diag_fix.py", "diag_page.py", "diag_pagination.py",
    "diag_why_empty.py", "probe_race.py", "verify_login_signals.py",
    "verify_fix.py", "verify_search.py",
    "extract_originals.py", "restore_originals.py", "verify_originals.py",
    "enumerate_search_versions.py", "extract_pristine.py",
    "jd_goods_test.json",
]
moved = []
for name in created:
    p = BASE / name
    if p.exists():
        shutil.move(str(p), str(ARCHIVE / name))
        moved.append(name)
print(f"  已移动 {len(moved)} 个文件 -> _recovered/created_by_agent/")
for m in moved:
    print(f"    - {m}")
missing = [n for n in created if n not in moved]
if missing:
    print(f"  (不存在，跳过: {', '.join(missing)})")

# _recovered 里的中间提取物也一并归入归档
for extra in ("search_versions",):
    p = REC / extra
    if p.exists():
        shutil.move(str(p), str(ARCHIVE / extra))

# ---------- 3. 最终校验 ----------
print("=" * 68)
print("步骤3：最终校验")
for name, expect in (("login.py", 146), ("search.py", 83)):
    t = (BASE / name).read_text(encoding="utf-8")
    n = len(t.splitlines())
    try:
        ast.parse(t)
        ok = "语法OK"
    except SyntaxError as e:
        ok = f"语法错误 {e}"
    flag = "OK" if (n == expect and ok == "语法OK") else "!!"
    print(f"  [{flag}] {name:12s} {n:4d} 行 / {len(t.encode('utf-8')):5d} 字节  {ok}")

print("=" * 68)
print("步骤4：工作区当前文件")
for p in sorted(BASE.iterdir()):
    kind = "DIR " if p.is_dir() else "FILE"
    size = "" if p.is_dir() else f"{p.stat().st_size:8d}"
    print(f"  {kind} {size}  {p.name}")
