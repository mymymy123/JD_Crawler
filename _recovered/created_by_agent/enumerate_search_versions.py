"""枚举 b1 里出现的所有 search.py 版本，定位"我修改前"的那一版。

区分依据：
- 本次会话我读到的版本：227 行、docstring 含"本文件不再在 import 时执行…"、无 GoodsCollector
- 更早的版本（会话前用户自己改过）：docstring 含"原 `from login import *`+模块级 login_jd()"
"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = Path(__file__).resolve().parent
SRC = Path(r"D:\pycharm_project\open_projects\JD_Crawler-acl-recovery"
           r"\b1_original_login_read.txt")
OUT = BASE / "_recovered" / "search_versions"
OUT.mkdir(parents=True, exist_ok=True)

lines = SRC.read_text(encoding="utf-8").splitlines()
dec = json.JSONDecoder()

versions = []
# A) 单行 JSON 信封形式
for i, l in enumerate(lines, 1):
    s = l.strip()
    if not s.startswith('{"content":"'):
        continue
    try:
        obj, _ = dec.raw_decode(s)
    except Exception:
        continue
    c = obj.get("content")
    if isinstance(c, str) and c.startswith('"""京东商品搜索抓取'):
        versions.append((i, "json_envelope", c))

# B) 块形式：{"content":"... 之后的普通文本（可能被截断）
for i, l in enumerate(lines, 1):
    if '{"content":"\\"\\"\\"京东商品搜索抓取' in l:
        versions.append((i, "block_envelope(截断风险)", "见下方文本形式"))
    if '"""京东商品搜索抓取' in l and not l.strip().startswith('{"content"'):
        versions.append((i, "plain_block", "见下行起文本"))

print("=" * 70)
print("在 b1 中发现的 search.py 候选：")
for idx, (lineno, kind, content) in enumerate(versions):
    if isinstance(content, str) and len(content) > 200:
        p = OUT / f"v{idx}_L{lineno}.py"
        p.write_text(content, encoding="utf-8")
        has_gc = "GoodsCollector" in content
        doc1 = "本文件不再在 import 时执行" in content
        doc0 = "原 `from login import *`" in content
        print(f"  [{idx}] line {lineno:5d} {kind:24s} {len(content.splitlines()):4d} 行"
              f"  GoodsCollector={has_gc}  会话版docstring={doc1}  更早版docstring={doc0}")
        print(f"        -> {p.name}")
    else:
        print(f"  [{idx}] line {lineno:5d} {kind:24s} {content}")
