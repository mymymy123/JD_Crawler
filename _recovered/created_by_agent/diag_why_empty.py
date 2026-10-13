"""诊断：新一轮抓取为什么拿不到 searchWare 数据。

只读脚本，不修改 login.py / search.py，也不写 Cookie 文件。

关注点：
1. 抓取时搜索页的**最终 URL**（是否被踢到登录页/风控页）
2. 页面发出的**所有接口 URL**（searchWare 是否改名、是否换成别的域名）
3. searchWare 响应的**内容类型与开头字节**（为什么 response.json() 失败）
4. `[data-sku]` 等卡片选择器的数量（DOM 兜底为什么也是 0）
"""
import json
from playwright.sync_api import sync_playwright

import login as L

SEARCH_URL = "https://search.jd.com/Search?keyword=%E6%89%8B%E6%9C%BA&enc=utf-8"

captured = []      # (url, status, content_type)
bodies = []        # searchWare 相关响应的原始文本


def on_response(resp):
    url = resp.url
    if any(k in url for k in ("searchWare", "Search", "search")):
        try:
            ct = resp.header_value("content-type") or ""
        except Exception:
            ct = "(err)"
        captured.append((url, resp.status, ct))
        if "searchWare" in url:
            try:
                bodies.append((url, resp.text()[:600]))
            except Exception as e:
                bodies.append((url, f"<text() failed: {type(e).__name__}: {e}>"))


def main():
    pw, browser, context, page = L.login_jd()

    try:
        context.on("response", on_response)

        print("=" * 70)
        print("步骤1：本地 Cookie 加载情况")
        names = L.cookie_names(context)
        print(f"  上下文 cookie 数: {len(names)}")
        print(f"  含 pt_key/pt_pin: {'pt_key' in names} / {'pt_pin' in names}")

        print("=" * 70)
        print("步骤2：打开搜索页并等待稳定")
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        stable = L.wait_until_stable(page, timeout=8.0)
        print(f"  最终 URL: {stable}")
        print(f"  是否登录页/风控页: {L.is_login_url(stable)}")
        print(f"  页面标题: {page.title()[:80]}")

        print("=" * 70)
        print("步骤3：登录态判定")
        ok, reason = L.check_login_status(page, context)
        print(f"  check_login_status -> {ok} / {reason}")

        print("=" * 70)
        print("步骤4：DOM 卡片选择器计数")
        for sel in ("[data-sku]", ".gl-item", "li.gl-item", "[class*='_pagination_next_']",
                    ".pn-next", "[class*='goods']", "[class*='card']"):
            try:
                n = page.locator(sel).count()
            except Exception as e:
                n = f"ERR {type(e).__name__}"
            print(f"  {sel:36s} -> {n}")

        print("=" * 70)
        print("步骤5：页面可见文本前 400 字")
        try:
            body = page.inner_text("body")
            print("  " + body[:400].replace("\n", " | "))
        except Exception as e:
            print(f"  inner_text 失败: {e}")

        print("=" * 70)
        print("步骤6：抓到的相关接口请求")
        seen = set()
        for url, status, ct in captured:
            key = url.split("?")[0]
            if key in seen:
                continue
            seen.add(key)
            print(f"  [{status}] {url[:150]}")
            print(f"        content-type={ct[:60]}")
        print(f"  合计 {len(captured)} 个响应，{len(seen)} 个不同端点")

        print("=" * 70)
        print("步骤7：searchWare 响应体开头（解释 json 解析失败）")
        if not bodies:
            print("  (没有 searchWare 响应)")
        for url, text in bodies[:3]:
            print(f"  URL: {url[:130]}")
            print(f"  BODY前600字: {text!r}")

        print("=" * 70)
        print("步骤8：检查是否有内联数据 / 前端配置")
        probe = page.evaluate("""() => {
            const html = document.documentElement.outerHTML;
            return {
                htmlLen: html.length,
                hasSkuInHtml: html.includes('data-sku'),
                hasSearchWareInHtml: html.includes('searchWare'),
                scripts: [...document.querySelectorAll('script')]
                    .map(s => s.src).filter(Boolean).slice(0, 12),
                markers: ['__NEXT_DATA__', 'window.__INITIAL', 'pageData',
                          'wareList', 'searchWare'].map(k => [k, html.includes(k)]),
            };
        }""")
        print(f"  HTML 长度: {probe['htmlLen']}")
        print(f"  含 data-sku: {probe['hasSkuInHtml']}  含 searchWare: {probe['hasSearchWareInHtml']}")
        print(f"  内联标记: {probe['markers']}")
        print("  脚本:")
        for s in probe["scripts"]:
            print(f"    {s[:110]}")

    finally:
        try:
            context.remove_listener("response", on_response)
        except Exception:
            pass
        print("=" * 70)
        print(">>> 保持 8 秒便于观察 <<<")
        page.wait_for_timeout(8000)
        L.cleanup(pw, browser)


if __name__ == "__main__":
    main()
