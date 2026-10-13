"""提取"最初"的 search.py（83 行，含模块级 all_goods/seen_ids 与导入即 login_jd()）。

会话数据中 search.py 存在两代：
  - 最初版：83 行，`all_goods = []` + `login_jd()` 在模块级，终止条件被注释掉
  - 修复版：227 行，引入 GoodsCollector
本脚本提取最初版，并与 146 行的原始 login.py 一起做最终校验。
"""
import ast
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = Path(__file__).resolve().parent
SRC = Path(r"D:\pycharm_project\open_projects\JD_Crawler-acl-recovery"
           r"\b1_original_login_read.txt")
OUT = BASE / "_recovered"

lines = SRC.read_text(encoding="utf-8").splitlines()

# 定位：含 "78:     return all_goods" 的 read 块，向上找 <content>，取其前 3 行 <path>
anchor = next(i for i, l in enumerate(lines) if l.strip() == "78:     return all_goods")
content_start = next(i for i in range(anchor, -1, -1) if lines[i].strip() == "<content>")
path_line = lines[content_start - 2]
print("来源:", path_line)
assert path_line.strip().endswith("search.py</path>"), path_line

body = []
i = content_start + 1
while i < len(lines):
    m = re.match(r"^(\d+): ?(.*)$", lines[i])
    if not m:
        break
    body.append(m.group(2))
    i += 1
text = "\n".join(body) + "\n"
(OUT / "original_search.py").write_text(text, encoding="utf-8")

print("=" * 62)
print(f"最初版 search.py: {len(body)} 行 / {len(text.encode('utf-8'))} 字节")
print("末行:", repr(lines[i]) if i < len(lines) else "(EOF)")
print()
print("--- 前 12 行 ---")
print("\n".join(body[:12]))
print("--- 末 8 行 ---")
print("\n".join(body[-8:]))
print()
print("特征校验：")
for m in ("all_goods = []", "seen_ids = set()", "playwright, browser, context, page = login_jd()",
          "page={page_no}", "template_url", "GoodsCollector", "return all_goods"):
    print(f"  {m:52s} {text.count(m)} 处")

print()
for name in ("original_login.py", "original_search.py"):
    t = (OUT / name).read_text(encoding="utf-8")
    try:
        ast.parse(t)
        ok = "语法OK"
    except SyntaxError as e:
        ok = f"语法错误: {e}"
    print(f"{name:24s} {len(t.splitlines()):4d} 行  {ok}")
