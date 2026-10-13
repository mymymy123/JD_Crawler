# 诊断 4：确认翻页机制（s 参数 / 下一页按钮）并验证 DOM 可用字段
import io
import json
import re
import sys
from urllib.parse import urlparse, parse_qs, unquote

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

TARGET = "pc_search_searchWare"
reqs = []


def on_response(response):
    if TARGET in response.url:
        q = parse_qs(urlparse(response.url).query)
        body_raw = unquote(q.get("body", [""])[0])
        try:
            body = json.loads(body_raw)
        except Exception:
            body = body_raw
        rec = {"s": (body or {}).get("s") if isinstance(body, dict) else None,
               "page": (body or {}).get("page") if isinstance(body, dict) else None,
               "pageSize": (body or {}).get("pageSize") if isinstance(body, dict) else None,
               "json": None}
        try:
            rec["json"] = response.json()
        except Exception as e:
            rec["json"] = {"__err__": str(e)}
        reqs.append(rec)


def dom_items(page):
    return page.evaluate("""() => {
        const out = [];
        for (const n of document.querySelectorAll('[data-sku]')) {
            const t = (n.innerText || '').replace(/\\s+/g, ' ').trim();
            const sku = n.getAttribute('data-sku');
            // 价格：抓 ¥ 后面的数字（含 1444 . 07 这种分离结构）
            let price = '';
            const pm = t.match(/¥\\s*([\\d,]+(?:\\.\\d+)?)\\s*(?:\\.\\s*(\\d+))?/);
            if (pm) price = pm[2] ? `${pm[1]}.${pm[2]}` : pm[1];
            // 名称：取第一个较长文本片段，或 title 属性
            const nm = t.split(/\\s{2,}|续航|骁龙|旗舰|现货|赠|支持/)[0];
            out.push({
                sku,
                price,
                text: t.slice(0, 150),
                title: n.getAttribute('title') || '',
                name_guess: (nm || '').slice(0, 60),
            });
        }
        return out;
    }""")


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False,
                                 args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="zh-CN")
    ctx.add_cookies(json.loads(open("jd_cookies.json", encoding="utf-8").read()))
    page = ctx.new_page()
    ctx.on("response", on_response)

    page.goto("https://search.jd.com/Search?keyword=%E6%89%8B%E6%9C%BA&enc=utf-8",
              wait_until="domcontentloaded")
    page.wait_for_timeout(7000)

    print("=" * 78)
    print("### 1. 首屏 searchWare 响应的 s / page / pageSize")
    print("=" * 78)
    for r in reqs:
        print(f"  s={r['s']}  page={r['page']}  pageSize={r['pageSize']}")

    print()
    print("=" * 78)
    print("### 2. 响应 JSON 结构（data 下的键 + 分页字段）")
    print("=" * 78)
    if reqs and isinstance(reqs[0]["json"], dict):
        j = reqs[0]["json"]
        print(f"  顶层键: {list(j.keys())}")
        data = j.get("data")
        if isinstance(data, dict):
            print(f"  data 键: {list(data.keys())}")
            for k, v in data.items():
                if not isinstance(v, (list, dict)):
                    print(f"    {k} = {v!r}")
            wl = data.get("wareList")
            if isinstance(wl, list) and wl:
                print(f"  wareList len = {len(wl)}")
                print(f"  wareList[0] 键 = {sorted(wl[0].keys())}")
                w0 = wl[0]
                for k in ("wareId", "wareName", "imageurl", "jdPrice", "finalPrice",
                          "good", "comments", "shopName", "venderId"):
                    if k in w0:
                        print(f"    {k} = {json.dumps(w0[k], ensure_ascii=False)[:160]}")

    print()
    print("=" * 78)
    print("### 3. 点击「下一页」按钮，观察 s 是否变化")
    print("=" * 78)
    for sel in ('[class*="_pagination_next_"]', '[class*="_fast_next_"]',
                '[class*="pagination"] [class*="next"]'):
        loc = page.locator(sel).first
        try:
            cnt = loc.count()
        except Exception as e:
            print(f"  {sel} -> ERR {e}")
            continue
        print(f"  {sel}: count={cnt}")
        if cnt:
            base = page.evaluate("() => document.querySelectorAll('[data-sku]').length")
            reqs.clear()
            try:
                loc.click(timeout=8000)
            except Exception as e:
                print(f"    点击失败: {type(e).__name__} {str(e)[:90]}")
                continue
            page.wait_for_timeout(8000)
            print(f"    点击后 URL: {page.url[:120]}")
            after = page.evaluate("() => document.querySelectorAll('[data-sku]').length")
            print(f"    点击后 data-sku 数: {after} (点击前 {base})")
            for r in reqs:
                print(f"    新请求 s={r['s']} page={r['page']} pageSize={r['pageSize']}")
            if reqs and isinstance(reqs[-1]["json"], dict):
                wl = (reqs[-1]["json"].get("data") or {}).get("wareList")
                if isinstance(wl, list) and wl:
                    ids = [w.get("wareId") for w in wl]
                    print(f"    新响应 wareList len={len(wl)} 前5={ids[:5]}")
            break

    print()
    print("=" * 78)
    print("### 4. DOM 抽取字段质量（前 6 条）")
    print("=" * 78)
    items = dom_items(page)
    print(f"共抽出 {len(items)} 条")
    for it in items[:6]:
        print(f"  sku={it['sku']:14s} price={it['price']:10s} title={it['title'][:30]!r}")
        print(f"      text={it['text'][:110]!r}")

    print()
    print(">>> 保持 8 秒 <<<")
    page.wait_for_timeout(8000)
    browser.close()
