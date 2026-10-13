# 诊断 2：页面到底返回了什么？searchWare 接口为何 0 命中？
import io
import json
import sys
from urllib.parse import urlparse, parse_qs, unquote

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

api_hits = []       # 所有像接口的响应
target_hits = []    # searchWare 命中


def on_response(response):
    url = response.url
    if any(k in url for k in ("searchAPI", "searchWare", "functionId", "api.jd",
                              "/api/", "es.jd", "search.jd.com/client")):
        if "searchWare" in url or "functionId" in url:
            target_hits.append((url, response.status))
        api_hits.append((response.status, url))


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False,
                                 args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="zh-CN")
    raw = json.loads(open("jd_cookies.json", encoding="utf-8").read())
    ctx.add_cookies(raw)
    page = ctx.new_page()
    ctx.on("response", on_response)

    url = "https://search.jd.com/Search?keyword=%E6%89%8B%E6%9C%BA&enc=utf-8"
    print(f"### 访问 {url}")
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(8000)

    print()
    print("=" * 78)
    print("### 页面基本体征")
    print("=" * 78)
    print(f"最终 URL   : {page.url}")
    print(f"title      : {page.title()!r}")

    for sel, desc in [("#J_goodsList", "商品列表容器"),
                      ("#J_goodsList .gl-item", "商品条目"),
                      (".gl-item", "商品条目(泛)"),
                      ("[data-sku]", "带 data-sku 的元素"),
                      (".pn-next", "下一页按钮"),
                      ("#J_bottomPage", "底部分页条"),
                      (".risk-mask, .verify, #J_verify", "风控/验证元素")]:
        try:
            n = page.locator(sel).count()
            print(f"  {desc:22s} {sel:28s} count={n}")
        except Exception as e:
            print(f"  {desc:22s} {sel:28s} ERR {type(e).__name__}")

    print()
    print("### 可见文本片段")
    try:
        body = page.locator("body").inner_text()[:700]
        print(body.replace("\n\n", "\n"))
    except Exception as e:
        print(f"读 body 失败: {e}")

    print()
    print("### HTML 中的商品痕迹")
    html = page.content()
    print(f"HTML 长度: {len(html)}")
    import re as _re
    for pat in (r"pc_search_searchWare", r"data-sku=\"(\d+)\"", r"//item\.jd\.com/(\d+)\.html",
                r"searchAPI", r"risk_handler", r"passport\.jd\.com"):
        m = _re.findall(pat, html)
        print(f"  {pat:34s} 命中 {len(m)} 次  样例={m[:4]}")

    print()
    print("=" * 78)
    print(f"### searchWare 命中数: {len(target_hits)}")
    print("=" * 78)
    for st, u in target_hits[:5]:
        q = parse_qs(urlparse(u).query)
        print(f"  status={st}")
        print(f"  body={unquote(q.get('body', [''])[0])[:300]}")

    print()
    print("=" * 78)
    print(f"### 其它接口响应（共 {len(api_hits)} 条，取前 25）")
    print("=" * 78)
    for st, u in api_hits[:25]:
        print(f"  [{st}] {u[:150]}")

    try:
        with sync_playwright() as _:
            pass
    except Exception:
        pass

    # 不用 input()，避免 EOF；直接留 15 秒观察窗口
    print()
    print(">>> 浏览器窗口将保持 15 秒，请看一眼页面实际内容 <<<")
    page.wait_for_timeout(15000)
    browser.close()
