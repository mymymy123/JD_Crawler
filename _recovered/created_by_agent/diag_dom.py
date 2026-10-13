# 诊断 3：新搜索页的 DOM 结构与实际接口
import io
import json
import re
import sys
from urllib.parse import urlparse, parse_qs, unquote

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

api_hits = []
target_hits = []


def on_response(response):
    url = response.url
    if any(k in url for k in ("searchAPI", "searchWare", "functionId", "api.jd",
                              "es.jd", "/api/", "client.action")):
        api_hits.append((response.status, url))
    if "searchWare" in url or "functionId" in url:
        target_hits.append((url, response.status))


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False,
                                 args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="zh-CN")
    ctx.add_cookies(json.loads(open("jd_cookies.json", encoding="utf-8").read()))
    page = ctx.new_page()
    ctx.on("response", on_response)

    page.goto("https://search.jd.com/Search?keyword=%E6%89%8B%E6%9C%BA&enc=utf-8",
              wait_until="domcontentloaded")
    page.wait_for_timeout(8000)

    print("=" * 78)
    print("### 1. searchWare 类接口命中（本次）")
    print("=" * 78)
    print(f"共 {len(target_hits)} 条")
    for u, st in target_hits:
        q = parse_qs(urlparse(u).query)
        print(f"  [{st}] {urlparse(u).netloc}{urlparse(u).path}")
        if "body" in q:
            print(f"        body = {unquote(q['body'][0])[:400]}")
        for k in ("functionId", "appid", "t"):
            if k in q:
                print(f"        {k} = {q[k][0]}")
        print(f"        完整URL前缀 = {u[:160]}")

    print()
    print("=" * 78)
    print("### 2. 其它接口响应（前 20）")
    print("=" * 78)
    for st, u in api_hits[:20]:
        print(f"  [{st}] {u[:170]}")

    print()
    print("=" * 78)
    print("### 3. DOM 商品结构（[data-sku] 元素）")
    print("=" * 78)
    info = page.evaluate("""() => {
        const nodes = [...document.querySelectorAll('[data-sku]')];
        const out = [];
        for (const n of nodes.slice(0, 4)) {
            const attrs = {};
            for (const a of n.attributes) attrs[a.name] = a.value.slice(0, 120);
            // 向上找可能的商品卡片容器
            let card = n, hops = 0;
            while (card.parentElement && hops < 6) {
                const cls = (card.className || '') + '';
                if (/item|card|goods|product/i.test(cls)) break;
                card = card.parentElement; hops++;
            }
            out.push({
                tag: n.tagName,
                classes: (n.className || '') + '',
                attrs,
                cardClasses: (card.className || '') + '',
                cardTag: card.tagName,
                text: (card.innerText || '').slice(0, 220),
            });
        }
        return {count: nodes.length, samples: out,
                parents: [...new Set(nodes.map(n => n.parentElement ?
                          n.parentElement.tagName + '.' + (n.parentElement.className||'') : ''))].slice(0,8)};
    }""")
    print(f"[data-sku] 总数 = {info['count']}")
    print(f"父容器候选     = {info['parents']}")
    for i, s in enumerate(info["samples"], 1):
        print()
        print(f"--- 样例 {i} ---")
        print(f"  tag={s['tag']} classes={s['classes']!r}")
        print(f"  卡片容器={s['cardTag']}.{s['cardClasses']!r}")
        print(f"  attrs={json.dumps(s['attrs'], ensure_ascii=False)[:300]}")
        print(f"  text={s['text']!r}")

    print()
    print("=" * 78)
    print("### 4. 页面内可能的商品数据变量 / 分页元素")
    print("=" * 78)
    probe = page.evaluate("""() => {
        const keys = Object.keys(window).filter(k =>
            /page|search|goods|ware|item|data/i.test(k)).slice(0, 40);
        const pager = [...document.querySelectorAll('a,div,span')]
            .filter(e => /下一页|next/i.test((e.className||'') + (e.getAttribute('href')||'')))
            .slice(0, 5)
            .map(e => e.tagName + '.' + (e.className||'') + ' href=' + (e.getAttribute('href')||''));
        return {winKeys: keys, pager};
    }""")
    print(f"window 相关键: {probe['winKeys']}")
    print(f"下一页候选   : {probe['pager']}")

    print()
    print("=" * 78)
    print("### 5. 翻页后 DOM 是否变化（对比 data-sku 集合）")
    print("=" * 78)
    first = page.evaluate("() => [...document.querySelectorAll('[data-sku]')].map(n => n.getAttribute('data-sku'))")
    print(f"page1 data-sku 数 = {len(first)}, 前 5 = {first[:5]}")

    for pn in (2, 3):
        page.goto(f"https://search.jd.com/Search?keyword=%E6%89%8B%E6%9C%BA&enc=utf-8&page={pn}",
                  wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        cur = page.evaluate("() => [...document.querySelectorAll('[data-sku]')].map(n => n.getAttribute('data-sku'))")
        print(f"page{pn} data-sku 数 = {len(cur)}, 前 5 = {cur[:5]}")
        print(f"      与 page1 交集 = {len(set(first) & set(cur))}, "
              f"独有 = {len(set(cur) - set(first))}")

    print()
    print(">>> 浏览器保持 10 秒 <<<")
    page.wait_for_timeout(10000)
    browser.close()
