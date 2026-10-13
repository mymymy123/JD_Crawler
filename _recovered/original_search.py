from login import *
import json
import time
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse,quote
import re

all_goods = []
seen_ids = set()
playwright, browser, context, page = login_jd()
template_url = None

def extract_goods(resp_json: dict) -> list:
    """从 pc_search_searchWare 响应中提取商品名称、最终价格、图片"""
    ware_list = resp_json.get("data", {}).get("wareList", []) or []
    goods = []

    for item in ware_list:
        # 1. 商品名：去掉搜索关键词高亮的 <font> 标签
        raw_name = item.get("wareName", "") or ""
        name = re.sub(r"<[^>]+>", "", raw_name)

        # 2. 最终价格：优先取国补/到手价，没有则退回京东价
        final_price = (item.get("finalPrice") or {}).get("estimatedPrice")
        price = final_price or item.get("jdPrice", "")

        # 3. 图片：相对路径补全 CDN 前缀
        img_path = item.get("imageurl", "") or ""
        img_url = f"https://img13.360buyimg.com/n7/{img_path}" if img_path else ""

        goods.append({
            "wareId": item.get("wareId"),
            "name": name,
            "price": price,
            "image": img_url,
        })

    return goods

def search_all_goods(keyword: str, max_page: int = 20):
    all_goods = []
    seen_ids = set()

    def on_response(response):
        if 'functionId=pc_search_searchWare' not in response.url:
            return
        try:
            for g in extract_goods(response.json()):   # 上一轮的提取函数
                if g["wareId"] not in seen_ids:
                    seen_ids.add(g["wareId"])
                    all_goods.append(g)
        except Exception as e:
            print(f"[解析失败] {e}")

    context.on('response', on_response)

    prev_count = 0
    no_growth = 0

    for page_no in range(1, max_page + 1):
        url = f"https://search.jd.com/Search?keyword={quote(keyword)}&enc=utf-8&page={page_no}"
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)   # 等 searchWare 接口返回

        new_count = len(all_goods)
        print(f"第 {page_no} 页: 新增 {new_count - prev_count} 条, 累计 {new_count} 条")
        prev_count = new_count
        page.wait_for_timeout(10000)

        # 终止条件：连续 2 页没有新数据（翻到底 / 触发风控）
        # if new_count == prev_count:
        #     no_growth += 1
        #     if no_growth >= 2:
        #         print("连续无新增，停止翻页")
        #         break
        # else:
        #     no_growth = 0

    return all_goods


# 使用
goods = search_all_goods("手机", max_page=20)
print(f"共 {len(goods)} 条")
