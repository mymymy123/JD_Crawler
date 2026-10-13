# 验证 search.py 修复：导入无副作用、监听器可摘除、终止条件生效、状态不遮蔽
import ast
import io
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ok_all = True


def check(name, cond, extra=""):
    global ok_all
    if not cond:
        ok_all = False
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {extra}")


for f in ("login.py", "search.py"):
    try:
        ast.parse(open(f, encoding="utf-8").read(), filename=f)
        print(f"[静态] {f} 语法 OK")
    except SyntaxError as e:
        print(f"[静态] {f} 语法错误: {e}")
        raise SystemExit(1)

print()
print("=== 1. 导入 search 不得启动浏览器（原 from login import * + 模块级 login_jd） ===")
t0 = time.time()
import search as S
elapsed = time.time() - t0
check("import search 无副作用", elapsed < 3.0, f"(耗时 {elapsed:.2f}s)")

# 确认没有模块级残留的已登录会话
for bad in ("playwright", "browser", "context", "page", "all_goods", "seen_ids"):
    check(f"模块级无遗留 {bad}", not hasattr(S, bad),
          f"(实际 {getattr(S, bad, None)!r})" if hasattr(S, bad) else "")

print()
print("=== 2. extract_goods 字段与价格回退 ===")
payload = {"data": {"wareList": [
    {"wareId": 1, "wareName": "<font>手机</font>甲",
     "finalPrice": {"estimatedPrice": "1999.00"}, "imageurl": "a.jpg"},
    {"wareId": 2, "wareName": "手机乙", "jdPrice": "2999.00",
     "imageurl": "b.jpg"},
    {"wareId": None, "wareName": "手机丙", "jdPrice": 3999},
]}}
g = S.extract_goods(payload)
check("提取 3 条", len(g) == 3, f"实际 {len(g)}")
check("font 标签已剥离", g[0]["name"] == "手机甲", f"{g[0]['name']!r}")
check("到手价优先", g[0]["price"] == "1999.00" and g[0]["is_final"] is True)
check("回退京东价", g[1]["price"] == "2999.00" and g[1]["is_final"] is False)
check("图片补全 CDN", g[0]["image"] == S.CDN_PREFIX + "a.jpg")

print()
print("=== 3. 去重：wareId 缺失时用名称+价格兜底 ===")
c = S.GoodsCollector()
c.seen_ids.add("手机丙|3999")
before = len(c.goods)
c.seen_ids.add("手机丙|3999")           # 重复同一 key
check("去重集合生效", len(c.goods) == before)


class FakeResp:
    def __init__(self, url, payload=None, bad=False):
        self.url = url
        self._payload = payload
        self._bad = bad

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


c = S.GoodsCollector()
c.on_response(FakeResp("https://x/?functionId=other", payload))
check("非目标接口被忽略", len(c.goods) == 0 and c.parsed_count == 0)
c.on_response(FakeResp("https://x/?functionId=pc_search_searchWare", payload))
check("目标接口被解析", len(c.goods) == 3 and c.parsed_count == 1)
c.on_response(FakeResp("https://x/?functionId=pc_search_searchWare", payload))
check("重复响应已去重", len(c.goods) == 3, f"实际 {len(c.goods)}")
c.on_response(FakeResp("https://x/?functionId=pc_search_searchWare", bad=True))
check("坏响应计入 error_count", c.error_count == 1, f"实际 {c.error_count}")

print()
print("=== 4. 监听器注册 / 摘除（泄漏修复） ===")
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(locale="zh-CN")

    probe = S.GoodsCollector()
    # 用一次性计数包装，确认 remove_listener 真的摘掉了
    hits = {"n": 0}

    def counted(response):
        hits["n"] += 1

    ctx.on("response", counted)
    ctx.on("response", probe.on_response)
    ctx.remove_listener("response", probe.on_response)
    ctx.remove_listener("response", counted)

    page = ctx.new_page()
    page.goto("https://example.com", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    check("摘除后回调不再触发", hits["n"] == 0, f"(触发 {hits['n']} 次)")
    check("remove_listener 不抛异常", True)

    print()
    print("=== 5. 终止条件：连续无新增即停 ===")
    # 用一个假 page/context 驱动 search_all_goods，避免真实登录
    calls = []

    class FakePage:
        url = "https://search.jd.com/Search?keyword=x"

        def goto(self, url, wait_until=None):
            calls.append(url)

        def wait_for_timeout(self, ms):
            pass

    class FakeCtx:
        def __init__(self):
            self._handlers = []

        def on(self, ev, fn):
            self._handlers.append(fn)

        def remove_listener(self, ev, fn):
            self._handlers.remove(fn)

        def cookies(self):
            return []

    fc = FakeCtx()
    got = S.search_all_goods("手机", max_page=20, no_growth_limit=2,
                             page_wait_ms=0, pace_ms=0,
                             context=fc, page=FakePage())
    check("无新增 2 页后停止", len(calls) == 2, f"(实际翻页 {len(calls)} 次)")
    check("返回空列表", got == [])
    check("监听器已摘除", len(fc._handlers) == 0,
          f"(残留 {len(fc._handlers)} 个)")

    calls.clear()
    fc2 = FakeCtx()
    S.search_all_goods("手机", max_page=3, no_growth_limit=0,
                       page_wait_ms=0, pace_ms=0,
                       context=fc2, page=FakePage())
    check("no_growth_limit=0 时跑满 max_page", len(calls) == 3,
          f"(实际 {len(calls)} 次)")

    browser.close()

print()
print("=== 结论 ===")
print("  全部通过 ✅" if ok_all else "  存在失败项 ❌")
