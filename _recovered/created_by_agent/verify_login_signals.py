# 诊断脚本：复现 login.py 的三个登录判定信号，找出误判来源
# 只读，不改动 login.py / search.py
import json
from playwright.sync_api import sync_playwright

SEARCH_URL = 'https://search.jd.com/Search?keyword=&enc=utf-8'


def sig1(cookies):
    names = {c["name"] for c in cookies}
    return ("pt_key" in names and "pt_pin" in names), f"pt_key={'pt_key' in names} pt_pin={'pt_pin' in names}"


def sig3(page):
    try:
        loc = page.locator(".nickname, .user_logout, #ttbar-login .nickname").first
        n = loc.count()
        if n == 0:
            return False, "no element matched (count=0)"
        return loc.is_visible(), f"count={n} visible={loc.is_visible()}"
    except Exception as e:
        return False, f"exception: {type(e).__name__}: {e}"


cookies = json.loads(open("jd_cookies.json", encoding="utf-8").read())

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True,
                                 args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(viewport={"width": 1366, "height": 768}, locale="zh-CN")

    print("=" * 60)
    print("A) 加载 cookies 之前（干净上下文）")
    print("=" * 60)
    ctx.add_cookies(cookies)
    after = ctx.cookies()
    print(f"add_cookies 前 ctx.cookies() = 0 条")
    print(f"add_cookies 后 ctx.cookies() = {len(after)} 条  (文件里 {len(cookies)} 条)")
    names = sorted({c["name"] for c in after})
    pt = [n for n in names if n.lower().startswith("pt_")]
    print(f"注入后 pt_* 类 cookies: {pt or '【无】'}")

    page = ctx.new_page()
    page.goto(SEARCH_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)

    print()
    print("=" * 60)
    print("B) 访问 search.jd.com 后的三个信号")
    print("=" * 60)
    print(f"最终 URL : {page.url}")

    s1, d1 = sig1(ctx.cookies())
    s2 = "passport.jd.com" not in page.url
    s3, d3 = sig3(page)
    print(f"信号1 Cookie pt_key+pt_pin : {s1}    ({d1})")
    print(f"信号2 URL 未在 passport 域 : {s2}    (url={page.url})")
    print(f"信号3 DOM 登录态元素       : {s3}    ({d3})")
    verdict = s1 or s2 or s3
    print(f"==> 三信号择一即判登录 => check_login_status() = {verdict}")
    print("    (真实状态：cookies 已失效 = 未登录)")

    # 页面上实际的登录入口文案，用于判断信号3是否被登录链接误命中
    print()
    print("--- 页面顶部登录相关元素实测 ---")
    for sel in [".nickname", ".user_logout", "#ttbar-login .nickname", "#ttbar-login",
                ".link-user", "#ttbar-login a.link-user"]:
        try:
            l = page.locator(sel).first
            c = l.count()
            vis = l.is_visible() if c else None
            txt = ""
            if c:
                try:
                    txt = (l.inner_text(timeout=1500) or "").strip().replace("\n", " ")[:40]
                except Exception:
                    txt = "<读不到文本>"
            print(f"  {sel:32s} count={c} visible={vis} text={txt!r}")
        except Exception as e:
            print(f"  {sel:32s} ERR {type(e).__name__}")

    print()
    print("--- 决定性检查：受保护页面（我的订单）是否被踢回登录页 ---")
    page.goto("https://order.jd.com/center/list.action", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    print(f"order.jd.com 最终 URL : {page.url}")
    print(f"首页标题             : {page.title()!r}")
    logged_in = "passport.jd.com" not in page.url and "login" not in page.url.lower()
    print(f"==> 该受保护页判定已登录 = {logged_in}  (真实应为 False)")

    browser.close()
