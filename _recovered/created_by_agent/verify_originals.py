"""校验提取出的 original_login.py / original_search.py 是否含"修改前"的特征标记。"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = Path(__file__).resolve().parent
D = BASE / "_recovered"

login_must = (  # 原始版本应存在的特征
    'COOKIE_FILE = Path("jd_cookies.json")',
    'if "passport.jd.com" not in page.url:',
    "def check_login_status(page, context) -> bool:",
)
login_must_not = (  # 我加的修复特征，原版不应有
    "BASE_DIR", "wait_until_stable", "is_login_url", "LOGIN_ANCHORS",
    "AUTH_COOKIE_KEYS", "settle",
)

search_must = (
    "from login import *",
    "login_jd()",
    "page={page_no}",
    "all_goods = []",
    "seen_ids = set()",
)
search_must_not = (
    "GoodsCollector", "remove_listener", "NEXT_BUTTON_SELECTORS",
    "DOM_EXTRACT_JS", "wait_change", "_ingest_dom", "extract_from_dom",
)

for name, must, must_not in (
    ("original_login.py", login_must, login_must_not),
    ("original_search.py", search_must, search_must_not),
):
    text = (D / name).read_text(encoding="utf-8")
    print("=" * 62)
    print(f"{name}  ({len(text.splitlines())} 行)")
    print("  应存在：")
    for m in must:
        print(f"    [{'OK ' if m in text else '缺失'}] {m}")
    print("  应不存在（我引入的修复特征）：")
    for m in must_not:
        n = text.count(m)
        print(f"    [{'干净' if n == 0 else f'发现 {n} 处'}] {m}")
