"""京东商品搜索抓取。

核心手法：不解析 HTML DOM，而是监听 XHR 响应，直接读取结构化的
`pc_search_searchWare` 接口数据——京东商品列表由脚本渲染，直接吃接口更稳。

依赖 login.py 提供的登录上下文。三个关键约定：
1. 本文件不再在 import 时执行任何登录/抓取（原 `from login import *` + 模块级
   `login_jd()` 会导致「导入即启动浏览器」），业务逻辑全部收进 main()。
2. 响应监听器用 try/finally 保证摘除，避免重复调用时监听器叠加。
3. 翻页务必在 finally 里 cleanup()，否则 Playwright/Chromium 进程会残留。
"""
import re
from urllib.parse import quote

import login as L

# 京东搜索接口标识，命中即说明本响应携带商品列表
SEARCHWARE_MARKER = "functionId=pc_search_searchWare"
CDN_PREFIX = "https://img13.360buyimg.com/n7/"


def extract_goods(resp_json: dict) -> list:
    """从 pc_search_searchWare 响应中提取商品名称、最终价格、图片。

    价格同时保留原始字段，避免类型混淆：
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
    """响应监听器 + 去重收集器。

    原实现把 all_goods / seen_ids 放在 search_all_goods 内部，
    却同时又在模块级声明同名变量，导致局部变量遮蔽模块变量（模块级那两行是死代码）。
    这里把状态收进实例，监听器与调用方共享同一份，彻底消除遮蔽。
    """

    def __init__(self):
        self.goods = []
        self.seen_ids = set()
        self.parsed_count = 0   # 成功解析的响应次数
        self.error_count = 0    # 解析失败的响应次数

    # 作为 context.on('response', ...) 的回调，注意必须绑定实例方法
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
            batch = extract_goods(payload)
        except Exception as e:
            self.error_count += 1
            print(f"[解析失败] 结构异常: {e}")
            return

        self.parsed_count += 1
        for g in batch:
            wid = g.get("wareId")
            # wareId 缺失时用「名称+价格」兜底去重，否则所有空 id 会被当成同一条
            key = wid or f"{g.get('name')}|{g.get('price')}"
            if key in self.seen_ids:
                continue
            self.seen_ids.add(key)
            self.goods.append(g)

    # 便捷代理
    def __len__(self):
        return len(self.goods)


def search_all_goods(keyword: str, max_page: int = 20,
                     no_growth_limit: int = 2,
                     page_wait_ms: int = 2500,
                     pace_ms: int = 10000,
                     context=None, page=None) -> list:
    """按关键词翻页抓取商品。

    :param max_page:        最多翻多少页
    :param no_growth_limit: 连续多少页无新增即停止（翻到底 / 触发风控）；设 0 关闭
    :param page_wait_ms:    每页等待接口返回的毫秒数
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

    try:
        # 登录态前置校验：避免像修复前那样，带着失效 Cookie 空翻 20 页拿 0 条
        ok, reason = L.check_login_status(page, context)
        if not ok:
            print(f"[!] 当前未处于登录态（{reason}），抓取很可能全部为空，请先完成登录")

        for page_no in range(1, max_page + 1):
            url = (f"https://search.jd.com/Search?keyword={quote(keyword)}"
                   f"&enc=utf-8&page={page_no}")

            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(page_wait_ms)

            if L.is_login_url(page.url):
                stopped_reason = f"第 {page_no} 页被重定向到登录页，登录态已失效"
                print(f"[!] {stopped_reason}")
                break

            new_count = len(collector.goods)
            added = new_count - prev_count
            print(f"第 {page_no} 页: 新增 {added} 条, 累计 {new_count} 条"
                  f"（已解析 {collector.parsed_count} 个响应）")
            prev_count = new_count

            # 终止条件：连续 no_growth_limit 页没有新数据（翻到底 / 触发风控）
            if added == 0:
                no_growth += 1
                if no_growth_limit and no_growth >= no_growth_limit:
                    stopped_reason = f"连续 {no_growth} 页无新增，停止翻页"
                    print(f"[*] {stopped_reason}")
                    break
            else:
                no_growth = 0

            if page_no < max_page:
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

    return collector.goods


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
    parser.add_argument("--max-page", type=int, default=20, help="最多翻页数")
    parser.add_argument("--no-growth-limit", type=int, default=2,
                        help="连续无新增多少页后停止，0 表示关闭")
    parser.add_argument("--out", default="jd_goods.json", help="结果文件名")
    args = parser.parse_args()

    try:
        goods = search_all_goods(args.keyword, max_page=args.max_page,
                                 no_growth_limit=args.no_growth_limit)
        print(f"共 {len(goods)} 条")
        if goods:
            save_goods(goods, args.out)
            for g in goods[:3]:
                print(f"  - {g['name'][:40]}  价格={g['price']}")
    except Exception as e:
        print(f"[!] 抓取失败: {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
