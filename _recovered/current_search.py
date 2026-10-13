"""京东商品搜索抓取。

核心手法：监听 XHR 响应，直接读取结构化的 `pc_search_searchWare` 接口数据。

⚠ 关于翻页（京东 2025 新版搜索页的重大变化）：
    静态 URL 里的 `&page=N` **已经完全失效**——实测带 page=1/2/3 访问，
    页面每次都请求 `s=1, page=1`，返回的 30 条商品完全相同。
    新版翻页由前端点击「下一页」触发，接口按 **偏移量 s** 分页
    （实测：`s=1 page=1`、`s=25 page=2`、`s=55 page=3`，其后每页 +30）：
        s=1   -> 第 1 页（page=1）
        s=25  -> 第 2 页（page=2）
        s=55  -> 第 3 页（page=3）   …… 每页 pageSize=30
    响应里的 `data.page` / `data.pageCount` 给出当前页与总页数。
    注意一次点击会连发多个预取响应（第 2、3 页可能一起回来），
    所以统计用「响应序号 + wareId 去重」，而不是「一次点击 = 一页」。

    因此本脚本改为：导航一次，然后循环点击「下一页」并按 `data.page` 计数。

依赖 login.py 提供的登录上下文。三个关键约定：
1. 本文件不在 import 时执行任何登录/抓取，业务逻辑全部收进 main()。
2. 响应监听器用 try/finally 保证摘除，避免重复调用时监听器叠加。
3. 抓取结束务必 cleanup()，否则 Playwright/Chromium 进程会残留。
"""
import re
from urllib.parse import quote

import login as L

# 京东搜索接口标识，命中即说明本响应携带商品列表
SEARCHWARE_MARKER = "functionId=pc_search_searchWare"
CDN_PREFIX = "https://img13.360buyimg.com/n7/"

# 新版搜索页使用 hash 类名（如 _pagination_next_1gv36_8），只能用包含匹配
NEXT_BUTTON_SELECTORS = (
    '[class*="_pagination_next_"]',
    '[class*="_fast_next_"]',
    'a.pn-next',
    '.pn-next',
)

# DOM 兜底：接口未命中时从卡片文本里抠商品名
#
# 卡片 innerText 形如（实测）：
#   "广告 vivo S60 元气版 8GB+256GB 星星海 湿手秒开… AI拍照手机 田曦薇 1万+人已买
#    |100万+人浏览 |拍照效果超清晰 领国家补贴减￥404.83 1万"
# 真实商品名止于规格，其后全是营销标签，所以用前瞻在「容量/万+人/已买/¥/|」处截断。
DOM_EXTRACT_JS = """() => {
    const NAME_RE = /^(?:(?:广告|自营|京东自营)\\s*)?(.+?)(?=\\s*(?:\\d+(?:\\.\\d+)?\\s*(?:GB|TB|G|T)\\b|\\d+万|\\d+人|已售|已买|京东自营|旗舰店|国家补贴|领国家补贴|\\u00a5|\\||：|:)|$)/i;
    const PRICE_RE = /\\u00a5\\s*([\\d,]+(?:\\.\\d+)?)\\s*(?:\\.\\s*(\\d{1,2}))?/;
    return [...document.querySelectorAll('[data-sku]')].map(n => {
        const sku = n.getAttribute('data-sku');
        const t = (n.innerText || '').replace(/\\s+/g, ' ').trim();
        let price = '';
        const pm = t.match(PRICE_RE);
        if (pm) price = pm[2] ? pm[1] + '.' + pm[2] : pm[1];
        let name = '';
        const m = t.match(NAME_RE);
        if (m) name = (m[1] || '').trim();
        return {wareId: sku, name: name, price: price, image: ''};
    });
}"""


def extract_goods(resp_json: dict) -> list:
    """从 pc_search_searchWare 响应中提取商品名称、最终价格、图片。

    价格同时保留来源字段，避免类型混淆：
      price    —— 展示用，优先到手价，否则京东价
      is_final —— price 是否为「到手价/国补价」
    """
    ware_list = (resp_json.get("data") or {}).get("wareList") or []
    goods = []

    for item in ware_list:
        # 1. 商品名：去掉搜索关键词高亮的 <font> 标签
        raw_name = item.get("wareName") or ""
        name = re.sub(r"<[^>]+>", "", raw_name)

        # 2. 价格：优先取国补/到手价，没有则退回京东价
        final_price = (item.get("finalPrice") or {}).get("estimatedPrice")
        jd_price = item.get("jdPrice")
        price = final_price or jd_price or ""
        is_final = bool(final_price)

        # 3. 图片：相对路径补全 CDN 前缀
        img_path = item.get("imageurl") or ""
        img_url = f"{CDN_PREFIX}{img_path}" if img_path else ""

        goods.append({
            "wareId": item.get("wareId"),
            "name": name,
            "price": price,
            "is_final": is_final,
            "image": img_url,
        })

    return goods


class GoodsCollector:
    """响应监听器 + 去重收集器 + 响应事件源。

    原实现把 all_goods / seen_ids 放在 search_all_goods 内部，却同时在模块级
    声明同名变量，造成局部变量遮蔽模块变量（模块级那两行为死代码）。
    这里把状态收进实例，监听器与调用方共享同一份，彻底消除遮蔽。

    同时充当「响应到达」事件源：调用方据此把「等页面加载完」换成
    「等真正的接口响应回来」，比固定 sleep 更可靠也更快。
    """

    def __init__(self):
        self.goods = []
        self.seen_ids = set()
        self.parsed_count = 0     # 成功解析的响应次数
        self.error_count = 0      # 解析失败的响应次数
        self.api_page = None      # 接口返回的 data.page，用于翻页进度判定
        self.page_count = None    # 接口返回的 data.pageCount，总页数
        self.result_count = None  # 接口返回的 data.resultCount
        self.serial = 0           # 响应序号，每次解析 +1
        self.first_event = False  # 是否已收到首个响应

    def on_response(self, response):
        if SEARCHWARE_MARKER not in response.url:
            return
        try:
            payload = response.json()
        except Exception as e:
            self.error_count += 1
            print(f"[解析失败] 响应非 JSON: {e}")
            return

        try:
            data = payload.get("data") or {}
            batch = extract_goods(payload)
        except Exception as e:
            self.error_count += 1
            print(f"[解析失败] 结构异常: {e}")
            return

        # 记录接口自报的分页信息（比数页数可靠）
        if data.get("page") is not None:
            self.api_page = data.get("page")
        if data.get("pageCount") is not None:
            self.page_count = data.get("pageCount")
        if data.get("resultCount") is not None:
            self.result_count = data.get("resultCount")

        self.parsed_count += 1
        self.serial += 1
        self.first_event = True

        for g in batch:
            wid = g.get("wareId")
            # wareId 缺失时用「名称+价格」兜底去重，否则所有空 id 会被当成同一条
            key = wid or f"{g.get('name')}|{g.get('price')}"
            if key in self.seen_ids:
                continue
            self.seen_ids.add(key)
            self.goods.append(g)

    def wait_first(self, page, timeout_ms: int = 30000) -> bool:
        """等待首个 searchWare 响应到达。"""
        waited = 0
        while not self.first_event:
            if waited >= timeout_ms:
                return False
            page.wait_for_timeout(200)
            waited += 200
        return True

    def wait_change(self, page, timeout_ms: int = 30000) -> bool:
        """等待下一次 searchWare 响应到达（用于确认翻页真的生效）。"""
        before = self.serial
        waited = 0
        while self.serial <= before:
            if waited >= timeout_ms:
                return False
            page.wait_for_timeout(200)
            waited += 200
        return True

    def __len__(self):
        return len(self.goods)


def extract_from_dom(page) -> list:
    """DOM 兜底抽取。

    京东会 A/B 测试不同前端实现（部分灰度版本不在 HTML 里内联数据、
    接口也会改名），接口一旦改名监听就会全部落空。此时直接读页面卡片：
    新版用 `[data-sku]`，老版用 `.gl-item[data-sku]`。

    注意：卡片 `title` 属性实测为空，商品名只能从 innerText 正则抠；
    且**价格是异步渲染的**（实测抓 DOM 那一刻 ¥ 价还没注入，30 条里
    price 全为空），所以 price 为空是正常现象，真实价格以接口为准。
    """
    try:
        items = page.evaluate(DOM_EXTRACT_JS)
    except Exception as e:
        print(f"[!] DOM 兜底抽取失败: {type(e).__name__}: {e}")
        return []

    goods = []
    for it in items:
        sku = it.get("wareId")
        if not sku:
            continue
        name = (it.get("name") or "").strip()
        price = (it.get("price") or "").strip()
        goods.append({
            "wareId": sku,
            "name": name,
            "price": price,
            "is_final": "国补" in name,   # DOM 里无法可靠区分，仅作占位
            "image": it.get("image") or "",
        })
    return goods


def _find_next_button(page):
    for sel in NEXT_BUTTON_SELECTORS:
        loc = page.locator(sel).first
        try:
            if loc.count():
                return loc, sel
        except Exception:
            continue
    return None, None


def search_all_goods(keyword: str, max_page: int = 20,
                     no_growth_limit: int = 2,
                     page_wait_ms: int = 8000,
                     pace_ms: int = 3000,
                     context=None, page=None) -> list:
    """按关键词抓取商品。

    翻页方式：导航一次，然后循环点击「下一页」（京东新版静态 page 参数已失效）。

    :param max_page:        最多抓多少页
    :param no_growth_limit: 连续多少页无新增即停止；设 0 关闭
    :param page_wait_ms:    等待接口响应的超时毫秒数（不是固定等待时长）
    :param pace_ms:         翻页之间的额外间隔，用于降低触发风控的概率
    :param context/page:    可复用的已登录上下文；不传则自行登录
    :return: 商品列表
    """
    owns_session = context is None

    if owns_session:
        pw, browser, context, page = L.login_jd()
    else:
        # 复用外部会话时，只借用不负责释放
        pw = browser = None

    collector = GoodsCollector()
    # 直接注册绑定方法，便于 finally 里精确摘除
    context.on("response", collector.on_response)

    prev_count = 0
    no_growth = 0
    stopped_reason = f"达到页数上限 max_page={max_page}"
    dom_fallback_count = 0

    try:
        # 登录态前置校验：避免带着失效 Cookie 空翻多页只拿 0 条
        try:
            ok, reason = L.check_login_status(page, context)
        except TypeError:
            raise SystemExit(
                "login.check_login_status() 签名不匹配——它现在返回 (bool, str) "
                "元组，请确认 login.py 已同步更新")
        if not ok:
            print(f"[!] 当前未处于登录态（{reason}），抓取很可能全部为空，请先完成登录")

        # 只需导航一次；keyword 同样只能靠 URL 传，page 交给前端
        url = f"https://search.jd.com/Search?keyword={quote(keyword)}&enc=utf-8"
        print(f"[*] 打开搜索页：{url}")
        page.goto(url, wait_until="domcontentloaded")

        if not collector.wait_first(page, timeout_ms=page_wait_ms):
            print(f"[!] {page_wait_ms}ms 内未捕获到 searchWare 接口响应，"
                  f"改用 DOM 兜底抽取")

        if L.is_login_url(page.url):
            stopped_reason = "搜索页被重定向到登录页，登录态已失效"
            print(f"[!] {stopped_reason}")

        # 首屏：趁早读走 pageCount/resultCount，避免被前端预取请求覆盖
        first_page = collector.api_page or 1
        total_pages = collector.page_count or 0
        dom_fallback_first = _ingest_dom(page, collector)
        dom_fallback_count += dom_fallback_first

        new_count = len(collector.goods)
        print(f"第 {first_page} 页: 新增 {new_count - prev_count} 条, "
              f"累计 {new_count} 条（接口响应 {collector.parsed_count} 个）")
        print(f"[*] 接口自报：共 {total_pages or '?'} 页 / "
              f"{collector.result_count or '?'} 条结果")
        prev_count = new_count

        # 逐页点击「下一页」
        while True:
            if L.is_login_url(page.url):
                stopped_reason = "翻页过程中被重定向到登录页，登录态已失效"
                break

            current = collector.api_page or first_page
            limit = min(max_page, total_pages) if total_pages else max_page
            if current >= limit:
                stopped_reason = (f"已到最后一页（接口报 {total_pages} 页，"
                                  f"本次上限 {limit} 页）")
                break

            btn, sel = _find_next_button(page)
            if btn is None:
                stopped_reason = "页面上找不到「下一页」按钮，停止翻页"
                break

            before = collector.serial
            try:
                btn.click(timeout=10000)
            except Exception as e:
                stopped_reason = f"点击「下一页」失败: {type(e).__name__}"
                print(f"[!] {stopped_reason}: {str(e)[:100]}")
                break

            got = collector.wait_change(page, timeout_ms=page_wait_ms)
            dom_added = _ingest_dom(page, collector)
            dom_fallback_count += dom_added

            new_count = len(collector.goods)
            added = new_count - prev_count
            prev_count = new_count
            api_added = collector.serial - before

            if not got:
                print(f"[!] 翻页后 {page_wait_ms}ms 内未收到新响应"
                      f"（可能被风控或按钮无效）")

            print(f"第 {collector.api_page or '?'} 页: 新增 {added} 条, "
                  f"累计 {new_count} 条（本次响应 {api_added} 个，"
                  f"DOM 补 {dom_added} 条）")

            if added == 0:
                no_growth += 1
                if no_growth_limit and no_growth >= no_growth_limit:
                    stopped_reason = f"连续 {no_growth} 页无新增，停止翻页"
                    break
            else:
                no_growth = 0

            if pace_ms:
                page.wait_for_timeout(pace_ms)

    finally:
        # 关键：无论正常结束还是异常，都必须摘除监听器
        try:
            context.remove_listener("response", collector.on_response)
        except Exception as e:
            print(f"[!] 摘除响应监听器失败: {e}")

        if owns_session:
            # 关键：自行登录的会话必须释放，否则浏览器进程残留
            L.cleanup(pw, browser)

    if not collector.goods:
        print(f"[!] 未抓到任何商品（{stopped_reason}）")
        print("    常见原因：登录态失效、关键词无结果、或已被风控拦截")
    else:
        print(f"[*] 停止原因：{stopped_reason}")
        if dom_fallback_count:
            print(f"[*] 其中 {dom_fallback_count} 条来自 DOM 兜底抽取")

    return collector.goods


def _ingest_dom(page, collector) -> int:
    """把当前页 DOM 里的商品并入收集器，返回本次新增条数。

    这是接口改名/未命中时的保险，正常情况下 DOM 与接口数据重复，
    去重后新增为 0。
    """
    added = 0
    for g in extract_from_dom(page):
        key = g["wareId"]
        if key in collector.seen_ids:
            continue
        collector.seen_ids.add(key)
        collector.goods.append(g)
        added += 1
    return added


def save_goods(goods: list, path: str = "jd_goods.json") -> None:
    """把结果落盘，便于后续分析。"""
    import json
    from pathlib import Path
    out = Path(L.BASE_DIR) / path
    out.write_text(json.dumps(goods, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"[+] 已写入 {out.name}（{len(goods)} 条）")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="京东商品搜索抓取")
    parser.add_argument("keyword", nargs="?", default="手机", help="搜索关键词")
    parser.add_argument("--max-page", type=int, default=20, help="最多抓取页数")
    parser.add_argument("--no-growth-limit", type=int, default=2,
                        help="连续无新增多少页后停止，0 表示关闭")
    parser.add_argument("--pace-ms", type=int, default=3000,
                        help="翻页间隔毫秒数，用于降低风控概率")
    parser.add_argument("--out", default="jd_goods.json", help="结果文件名")
    args = parser.parse_args()

    goods = search_all_goods(args.keyword, max_page=args.max_page,
                             no_growth_limit=args.no_growth_limit,
                             pace_ms=args.pace_ms)
    print(f"共 {len(goods)} 条")
    if goods:
        save_goods(goods, args.out)
        print("[*] 示例：")
        for g in goods[:5]:
            print(f"  - {g['name'][:38]:38s} 价格={g['price']}")


if __name__ == "__main__":
    main()
