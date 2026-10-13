from login import *
from save_data import save_to_json, save_to_mongo, _keyword_slug,save_to_mongo
import time
import random
from urllib.parse import quote
import re


# 登录会话改到 main() 里创建，这里只留引用，供 search_all_goods 和 cleanup 共用。
# 放在模块级会导致 import search 就弹出浏览器跑一遍抓取，这里顺手修掉。
playwright = browser = context = page = None

# 搜索关键词。换商品改这一行就行，比如 "笔记本电脑"、"AirPods"。
KEYWORD = "手机"
MAX_ROUND=60

# 两轮加载之间的间隔（毫秒）。不管是「滚到底端」还是点「下一页」，每轮之间都等这么久。
# 这是控制请求频率、避开风控的主开关：被验证码/风控拦住就调大，太慢就调小。
# 参考值：3000 会触发验证，8000 是当前的安全起点。
ROUND_INTERVAL_MS = 8000
# 间隔的随机浮动区间。实际等待 = ROUND_INTERVAL_MS * uniform(下限, 上限)。
# 每轮都精确隔 8.000 秒本身就是机器节拍，加抖动更像人手动翻页。
ROUND_JITTER = (0.8, 1.4)

# 轮询接口返回时的步长（毫秒）
POLL_STEP_MS = 200
# 首屏等接口的时长（毫秒）。实测首屏接口经常什么都不返回，首批商品是第一轮滚动时才
# 到的，所以这个窗口给得很短——久等无益。
FIRST_SCREEN_WAIT_MS = 2000
# 每轮"等这批数据对应的接口返回"的超时（毫秒）。等不到并不代表数据丢了：
# on_response 一直在监听，迟到的响应照样会进 all_goods，只是算到后面的轮次里，
# 所以这个值可以给得比较小，省下的是零新增轮次的空等时间。
RESPONSE_WAIT_MS = 4000


# 「下一页」按钮的候选选择器。京东改版频繁，多备几个，最后再退回按文案查找。
NEXT_BUTTON_SELECTORS = (
    "#J_bottomPage .pn-next",
    ".pn-next",
    "a.pn-next",
    "button.pn-next",
)


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

def scroll_to_bottom(page, step: int = 1200, pause_ms: int = 400,
                     max_steps: int = 120) -> bool:
    """分步把页面滚到底端。"""
    page.mouse.move(400, 400)   # 让滚轮事件落在正文区域上

    last_y = -1
    for _ in range(max_steps):
        y = page.evaluate("window.scrollY")
        # 页面总高 - 视口高 = 能滚到的最大 scrollY
        bottom = page.evaluate(
            "Math.max(document.body.scrollHeight,"
            " document.documentElement.scrollHeight) - window.innerHeight")

        if y >= bottom - 5:     # 已经贴底
            return True
        if y == last_y:         # 发了滚轮事件但页面没动，说明滚不动了
            return False

        last_y = y
        page.mouse.wheel(0, step)
        page.wait_for_timeout(pause_ms)

    return False


def find_next_page_button(page):
    """在页面底部找可点击的「下一页」按钮。
    """
    for sel in NEXT_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel).first
            if not loc.count() or not loc.is_visible():
                continue
            if "disabled" in (loc.get_attribute("class") or ""):
                continue
            return loc
        except Exception:
            continue

    try:
        #exact=True 是精确匹配
        loc = page.get_by_text("下一页", exact=True).first
        if loc.count() and loc.is_visible():
            return loc
    except Exception:
        pass

    return None


# 京东限流提示页的文案特征
BLOCK_MARKERS = ("访问频繁", "无法搜索")


def is_blocked(page) -> bool:
    """当前页面是不是京东的「访问频繁」限流提示页。"""
    try:
        return any(page.get_by_text(m).count() > 0 for m in BLOCK_MARKERS)
    except Exception:
        return False


def search_all_goods(keyword: str, max_round: int = 60,
                     no_growth_limit: int = 1) -> list:
    """抓取关键词下的全部商品。

    :param max_round:       最多加载多少轮，防止死循环
    :param no_growth_limit: 滚动连续多少轮无新增后，改用「下一页」按钮
    :return: 商品列表
    """
    all_goods = []
    seen_ids = set()
    resp_count = 0  

    def on_response(response):
        nonlocal resp_count
        if 'functionId=pc_search_searchWare' not in response.url:
            return
        try:
            resp_count += 1
            for g in extract_goods(response.json()):   
                if g["wareId"] not in seen_ids:
                    seen_ids.add(g["wareId"])
                    all_goods.append(g)
        except Exception as e:
            print(f"[解析失败] {e}")

    context.on('response', on_response)

    url = f"https://search.jd.com/Search?keyword={quote(keyword)}&enc=utf-8"
    page.goto(url, wait_until="domcontentloaded")


    waited = 0
    while resp_count == 0 and waited < FIRST_SCREEN_WAIT_MS:
        page.wait_for_timeout(POLL_STEP_MS)
        waited += POLL_STEP_MS

    prev_count = len(all_goods)
    print(f"首屏: 累计 {prev_count} 条")

    no_growth = 0
    complete = False   # 只有真正抓到最后一页才置 True
    stopped_reason = f"达到轮次上限 max_round={max_round}"

    for round_no in range(1, max_round + 1):
        t0 = time.time()
        before = resp_count

        if is_blocked(page):
            stopped_reason = "被京东限流，页面提示「访问频繁导致无法搜索」"
            break

        if no_growth < no_growth_limit:
            action = "滚动到底端" if scroll_to_bottom(page) else "滚动（滚不动了）"
        else:
            btn = find_next_page_button(page)
            if btn is None:
                stopped_reason = "滚动无新增，且页面上没有可点的「下一页」按钮"
                complete = True
                break
            action = "下一页"
            try:
                btn.click(timeout=10000)
            except Exception as e:
                stopped_reason = f"点击「下一页」失败: {type(e).__name__}"
                print(f"[!] {stopped_reason}: {str(e)[:100]}")
                break

    
        waited = 0
        while resp_count <= before and waited < RESPONSE_WAIT_MS:
            page.wait_for_timeout(POLL_STEP_MS)
            waited += POLL_STEP_MS

    
        pace_ms = int(ROUND_INTERVAL_MS * random.uniform(*ROUND_JITTER))

        added = len(all_goods) - prev_count
        prev_count = len(all_goods)
        
        print(f"第 {round_no} 轮（{action}）: 新增 {added} 条, 累计 {prev_count} 条, "
              f"耗时 {time.time() - t0:.1f}s, 下轮间隔 {pace_ms / 1000:.1f}s")

        no_growth = 0 if added else no_growth + 1
        page.wait_for_timeout(pace_ms)

    
    if complete:
        print(f"[*] 已抓到底：{stopped_reason}")
    else:
        print(f"[!] 提前停止，结果可能不完整：{stopped_reason}")

    return all_goods


def main():
    global playwright, browser, context, page
    playwright, browser, context, page = login_jd()

    out_file = f"jd_goods_{_keyword_slug(KEYWORD)}.json"

    try:
        goods = search_all_goods(KEYWORD, max_round=MAX_ROUND)
        print(f"共 {len(goods)} 条")
        if goods:
            save_to_json(goods, out_file)
            try:
                save_to_mongo(goods, KEYWORD)
            except Exception as e:
                print(f"[!] 写入 MongoDB 失败（JSON 已保存，本轮结果没丢）: "
                      f"{type(e).__name__}: {e}")
        else:
            print(f"[!] 没有抓到任何商品，本次不覆盖 {out_file}")
    finally:
        cleanup(playwright, browser)


if __name__ == "__main__":
    main()
