# 诊断：为什么带 page=N 的第 2、3 页返回与第 1 页相同的数据
# 只读探测：不改动 search.py / login.py，不写任何业务文件
import io
import json
import sys
from urllib.parse import urlparse, parse_qs, unquote

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

TARGET = "pc_search_searchWare"
records = []


def on_response(response):
    if TARGET not in response.url:
        return
    q = parse_qs(urlparse(response.url).query)
    rec = {
        "url": response.url,
        "body": unquote(q.get("body", [""])[0]),
        "appid": q.get("appid", [""])[0],
        "status": response.status,
        "json": None,
        "err": None,
    }
    try:
        rec["json"] = response.json()
    except Exception as e:
        rec["err"] = f"{type(e).__name__}: {e}"
    records.append(rec)


def summarize(rec, idx):
    print("=" * 78)
    print(f"响应 #{idx}  status={rec['status']}  appid={rec['appid']}")
    print(f"  body : {rec['body'][:260]}")
    if rec["err"]:
        print(f"  JSON 解析失败: {rec['err']}")
        return None
    j = rec["json"]
    print(f"  顶层键: {list(j.keys())}")
    data = j.get("data")
    print(f"  data 类型: {type(data).__name__}")
    if not isinstance(data, dict):
        return None
    print(f"  data 键: {list(data.keys())}")
    ware = data.get("wareList")
    n = len(ware) if isinstance(ware, list) else "N/A"
    print(f"  wareList: type={type(ware).__name__} len={n}")
    if isinstance(ware, list) and ware:
        ids = [w.get("wareId") for w in ware]
        print(f"  前 8 个 wareId: {ids[:8]}")
        print(f"  本响应内不同 wareId 数: {len(set(ids))}")
        print(f"  首条键: {sorted(ware[0].keys())}")
    for k in ("page", "pageNo", "totalPage", "totalCount", "hasMore",
              "pageSize", "count"):
        if k in data:
            print(f"  分页字段 {k} = {data[k]!r}")
    return ware


def collect(page_no, wait_ms=5000, keyword="%E6%89%8B%E6%9C%BA"):
    records.clear()
    if page_no is None:
        url = f"https://search.jd.com/Search?keyword={keyword}&enc=utf-8"
    else:
        url = f"https://search.jd.com/Search?keyword={keyword}&enc=utf-8&page={page_no}"
    print()
    print("#" * 78)
    print(f"### page={page_no}  ->  {url}")
    print("#" * 78)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(wait_ms)
    print(f"[goto 后 URL] {page.url[:120]}")
    print(f"[命中 {len(records)} 个 {TARGET} 响应]")

    all_wares = []
    for i, r in enumerate(records, 1):
        w = summarize(r, i)
        if isinstance(w, list):
            all_wares.extend(w)

    ids = [w.get("wareId") for w in all_wares]
    print(f"--> page={page_no}: 合计 {len(all_wares)} 条, "
          f"不同 wareId {len(set(ids))} 个")
    try:
        h = page.evaluate("document.body.scrollHeight")
        print(f"--> 页面 scrollHeight = {h}")
    except Exception as e:
        print(f"--> 读 scrollHeight 失败: {e}")
    return ids


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False,
                                 args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(viewport={"width": 1366, "height": 768}, locale="zh-CN")
    raw = json.loads(open("jd_cookies.json", encoding="utf-8").read())
    ctx.add_cookies(raw)
    page = ctx.new_page()

    # 确认是否真的能免登录访问
    page.goto("https://search.jd.com/Search?keyword=%E6%89%8B%E6%9C%BA&enc=utf-8",
              wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    names = {c["name"] for c in ctx.cookies()}
    print(f"[上下文实际 cookie 数] {len(names)}")
    print(f"[pt_* 类] {sorted(n for n in names if n.lower().startswith('pt_')) or '无'}")
    print(f"[关键词] pin={'pin' in names} thor={'thor' in names} "
          f"unick={'unick' in names}")
    print(f"[当前 URL] {page.url[:120]}")

    ids1 = collect(1)
    ids2 = collect(2)
    ids3 = collect(3)

    set1, set2, set3 = set(ids1), set(ids2), set(ids3)
    print()
    print("=" * 78)
    print("### 三页对照")
    print("=" * 78)
    print(f"page1 不同 wareId: {len(set1)}")
    print(f"page2 不同 wareId: {len(set2)}")
    print(f"page3 不同 wareId: {len(set3)}")
    print(f"page1∩page2 交集: {len(set1 & set2)}")
    print(f"page1∩page3 交集: {len(set1 & set3)}")
    print(f"三页并集        : {len(set1 | set2 | set3)}")
    if set2:
        print(f"page2 独有      : {len(set2 - set1)}")
    if set1 == set2 == set3 and set1:
        print(">>> 结论：三页返回完全相同的商品集合（page 参数未生效）")

    # 试验：滚动触发懒加载
    print()
    print("#" * 78)
    print("### 试验：首页滚动到底，看是否懒加载更多")
    print("#" * 78)
    records.clear()
    page.goto("https://search.jd.com/Search?keyword=%E6%89%8B%E6%9C%BA&enc=utf-8",
              wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    before = len(records)
    print(f"首屏命中 {before} 个响应")
    for i in range(8):
        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(1200)
    after = len(records)
    print(f"滚动 8 次后命中 {after} 个响应（增量 {after - before}）")
    for i, r in enumerate(records, 1):
        summarize(r, i)

    # 试验：hover / 点击下一页看真实 URL 怎么变
    print()
    print("#" * 78)
    print("### 试验：点击页面「下一页」按钮，观察真实导航")
    print("#" * 78)
    for sel in ("#J_bottomPage .pn-next", "a.pn-next", ".pn-next",
                "#J_bottomPage a:nth-last-child(2)"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                print(f"找到下一页选择器: {sel}")
                with page.expect_navigation(timeout=15000):
                    loc.click()
                print(f"点击后 URL: {page.url[:160]}")
                break
        except Exception as e:
            print(f"  {sel} -> {type(e).__name__}: {str(e)[:80]}")
    else:
        print("未找到可点击的「下一页」按钮")

    input("按回车结束...")
    browser.close()
