"""从 b1 反压缩文件里提取 login.py / search.py 的原始内容（恢复用）。

b1 里保存着会话中 read/工具输出，包含修复前的 login.py 原始内容，
以及后续几个中间版本，用于回滚比对。
"""
import json
from pathlib import Path

SRC = Path(r"D:\pycharm_project\open_projects\JD_Crawler-acl-recovery"
           r"\b1_original_login_read.txt")
# 输出到工作区内（acl-recovery 目录对沙箱不可写，虽然 decompress 能写）
OUT = Path(__file__).resolve().parent / "_recovered"
OUT.mkdir(exist_ok=True)

dec = json.JSONDecoder()
lines = SRC.read_text(encoding="utf-8").splitlines()

found = []

for i, line in enumerate(lines, 1):
    s = line.strip()
    if not (s.startswith("{") and ("login.py" in s[:200] or "search.py" in s[:200])):
        continue
    try:
        obj, end = dec.raw_decode(s)
    except Exception:
        continue
    if not isinstance(obj, dict) or "content" not in obj:
        continue
    content = obj["content"]
    if not isinstance(content, str) or not content.startswith('"""京东'):
        continue
    if "京东登录模块" in content[:60]:
        name = "login.py"
    elif "京东商品搜索抓取" in content[:60]:
        name = "search.py"
    else:
        name = "unknown"
    kind = "full_file" if "file_path" not in obj else "read_replace"
    out = OUT / f"recovered_L{i}_{name}"
    try:
        out.write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"[!] 写 {out} 失败: {e}")
        continue
    found.append((i, kind, name, len(content), len(content.splitlines()), out))

print("提取到 %d 个 Python 文件版本：" % len(found))
for i, kind, name, nchars, nlines, out in found:
    print(f"  line {i:5d}  {kind:13s} {name:10s} {nlines:4d} 行 / {nchars:6d} 字符"
          f"  -> {out.name}")

print()
for i, kind, name, nchars, nlines, out in found:
    text = out.read_text(encoding="utf-8")
    marks = [m for m in ("def check_login_status", "def login_jd", "def cleanup",
                         "def save_cookies", "def load_cookies",
                         "def wait_login_success", "def main",
                         "def search_all_goods", "def extract_goods")
             if m in text]
    sig = [l for l in text.splitlines() if l.startswith("def check_login_status")]
    print(f"line {i} {name}: 关键定义 {len(marks)} 个 -> {marks}")
    if sig:
        print(f"        {sig[0]}")
