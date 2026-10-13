# 验证修复：失效 Cookie 必须被判为未登录（原先会误报成功）
import ast
import io
import json
import sys

# 强制 UTF-8 输出，避免 GBK 控制台乱码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# --- 静态校验 ---
for f in ("login.py", "search.py"):
    try:
        ast.parse(open(f, encoding="utf-8").read(), filename=f)
        print(f"[静态] {f} 语法 OK")
    except SyntaxError as e:
        print(f"[静态] {f} 语法错误: {e}")
        raise SystemExit(1)

import login as L

# 本次验证会调用 save_cookies，它写入的是真实存档路径；
# 先把原文件内容读进内存，结束时原样还原，避免破坏真实 Cookie。
_orig_path = L.COOKIE_FILE
_orig_bytes = _orig_path.read_bytes() if _orig_path.exists() else None
print(f"[保护] 已备份 {_orig_path.name}（{len(_orig_bytes) if _orig_bytes else 0} 字节），结束时还原")

ok_all = True
try:
    print()
    print("=== 1. 纯函数单元校验 ===")
    cases = [
        ("https://passport.jd.com/new/login.aspx?ReturnUrl=x", True),
        ("https://cfe.m.jd.com/privatedomain/risk_handler/03101900/?evApi=x", True),
        ("https://search.jd.com/Search?keyword=", False),
        ("https://www.jd.com/", False),
        ("https://order.jd.com/center/list.action", False),
    ]
    for url, expect in cases:
        got = L.is_login_url(url)
        flag = "PASS" if got == expect else "FAIL"
        if got != expect:
            ok_all = False
        print(f"  [{flag}] is_login_url({url[:50]:50s}) = {got} (期望 {expect})")

    print()
    print("=== 2. load_cookies：当前存档缺 pt_key，应判失效 ===")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="zh-CN")

        loaded = L.load_cookies(ctx)
        print(f"  load_cookies() = {loaded}   (期望 False，因为存档无 pt_key)")
        if loaded is not False:
            ok_all = False

        print()
        print("=== 3. 复现原误判场景：带上失效 Cookie 访问搜索页 ===")
        raw = json.loads(_orig_bytes.decode("utf-8")) if _orig_bytes else []
        ctx.add_cookies(raw)
        page = ctx.new_page()
        page.goto(L.SEARCH_URL, wait_until="domcontentloaded")
        print(f"  goto 返回瞬间 URL : {page.url[:96]}")

        # 新逻辑：settle=True 会先等跳转链走完
        ok, reason = L.check_login_status(page, ctx, settle=True)
        print(f"  check_login_status(settle=True) = {ok}")
        print(f"    依据            : {reason}")
        print(f"    判定时 URL      : {page.url[:96]}")
        if ok is not False:
            ok_all = False
            print("  [FAIL] 仍然误判为已登录")

        print()
        print("=== 4. wait_until_stable 返回的是否为终态 ===")
        page2 = ctx.new_page()
        page2.goto(L.SEARCH_URL, wait_until="domcontentloaded")
        print(f"  goto 返回瞬间     : {page2.url[:96]}")
        final = L.wait_until_stable(page2)
        print(f"  wait_until_stable : {final[:96]}")
        if "passport.jd.com" not in final:
            print("  [WARN] 终态不在 passport（可能页面未完成跳转）")
        else:
            print("  [PASS] 已稳定在登录页")

        print()
        print("=== 5. save_cookies 凭证计数 ===")
        total, auth = L.save_cookies(ctx)
        print(f"  返回 (total={total}, auth={auth})   (auth 期望 0)")

        browser.close()
finally:
    # 还原真实存档
    if _orig_bytes is not None:
        _orig_path.write_bytes(_orig_bytes)
        print()
        print(f"[还原] {_orig_path.name} 已恢复为原始 {len(_orig_bytes)} 字节")

print()
print("=== 结论 ===")
print("  全部通过 ✅" if ok_all else "  存在失败项 ❌")
