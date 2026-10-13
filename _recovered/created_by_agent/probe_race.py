# 竞态探测：page.goto(wait_until="domcontentloaded") 返回瞬间 URL 是否还在 search.jd.com
# login.py 的 load_cookies 分支就是在这个瞬间调用 check_login_status 的
import json
import time
from playwright.sync_api import sync_playwright

SEARCH_URL = 'https://search.jd.com/Search?keyword=&enc=utf-8'


def mark(t, label, url):
    print(f"[{t:6.3f}s] {label:38s} url={url}")


cookies = json.loads(open("jd_cookies.json", encoding="utf-8").read())

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True,
                                 args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(viewport={"width": 1366, "height": 768}, locale="zh-CN")
    ctx.add_cookies(cookies)
    page = ctx.new_page()

    print("=== 复现 login.py:91-96 的判定时序 ===")
    t0 = time.time()
    page.goto(SEARCH_URL, wait_until="domcontentloaded")
    t_goto = time.time() - t0
    mark(t_goto, "goto() 返回瞬间（login.py 在此判定）", page.url)

    url_at_check = page.url
    sig1 = False  # pt_key/pt_pin 已知不存在
    sig2 = "passport.jd.com" not in url_at_check
    try:
        sig3 = page.locator(".nickname, .user_logout, #ttbar-login .nickname").first.is_visible()
    except Exception:
        sig3 = False
    verdict = sig1 or sig2 or sig3
    print()
    print(f"  sig1(pt_key)  = {sig1}")
    print(f"  sig2(URL域)   = {sig2}   <-- 关键：此刻 URL 是否已跳到 passport")
    print(f"  sig3(DOM)     = {sig3}")
    print(f"  ==> check_login_status() = {verdict}")
    if verdict:
        print("  ==> login.py:93 打印 '[OK] Cookie 有效，跳过登录' 并 return  <<< 误判发生")
    print()

    print("=== 继续观察 URL 如何演化 ===")
    for s in (0.5, 1.0, 2.0, 4.0, 8.0):
        page.wait_for_timeout(int((s - (time.time() - t0 - t_goto)) * 1000) if s > (time.time() - t0 - t_goto) else 0)
        mark(time.time() - t0, f"goto 后 {s}s", page.url)

    # 误判后 search.py 第一页翻页会 goto 同一个 URL，观察是否立刻被踢
    print()
    print("=== login.py 误判 return 后，search.py:61 首次翻页 goto 同一 URL ===")
    page.goto(SEARCH_URL, wait_until="domcontentloaded")
    mark(time.time() - t0, "第二次 goto 返回瞬间", page.url)
    page.wait_for_timeout(3000)
    mark(time.time() - t0, "3s 后", page.url)

    browser.close()
