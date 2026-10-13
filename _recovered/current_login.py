"""京东登录模块（Playwright 同步 API）。

设计要点：
1. 登录态判定必须建立在「稳定终态」上——`goto(wait_until="domcontentloaded")`
   返回时页面可能还停在京东风控中转页 cfe.m.jd.com 上，此时任何基于 URL 的判定
   都会误判为「已离开登录页 = 已登录」。
2. 判定以 Cookie 凭证（pt_key / pt_pin）为唯一可信依据，URL 与 DOM 仅作辅助，
   且一律用「锚点判定」（明确落在登录页 = 未登录），不用「不在某域 = 已登录」
   这种弱判定。
3. Playwright 生命周期由调用方控制（用 start() 而非 with），因此 login_jd()
   返回四元组，用完务必调用 cleanup()。
"""
import json
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# 用绝对路径，避免调用方工作目录不同导致读错/写错 Cookie 文件
BASE_DIR = Path(__file__).resolve().parent
COOKIE_FILE = BASE_DIR / "jd_cookies.json"

LOGIN_URL = "https://passport.jd.com/new/login.aspx"
HOME_URL = "https://www.jd.com"
SEARCH_URL = "https://search.jd.com/Search?keyword=&enc=utf-8"

# 登录凭证：京东登录成功后写入，是最可靠的已登录标志
AUTH_COOKIE_KEYS = ("pt_key", "pt_pin")
# 辅助凭证：pt_key 偶尔缺失时可佐证登录态，但不单独作为判定依据
AUX_COOKIE_KEYS = ("pin", "thor", "unick")

# 判定「已离开登录页」时用来识别登录页/风控页的锚点
_LOGIN_URL_RE = re.compile(r"passport\.jd\.com|/login|risk_handler", re.I)
# DOM 登录态元素（多写几个候选选择器，京东改版频繁）
LOGGED_IN_SELECTORS = (
    ".nickname, .user_logout, #ttbar-login .nickname, "
    ".link-user, #ttbar-login a.link-user, .user-info .name"
)


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def cookie_names(context) -> set:
    """读取当前上下文中实际生效的 Cookie 名集合。"""
    try:
        return {c["name"] for c in context.cookies()}
    except Exception:
        return set()


def has_auth_cookie(context) -> bool:
    """是否同时具备 pt_key 与 pt_pin —— 这是京东真正的登录凭证。"""
    names = cookie_names(context)
    return all(k in names for k in AUTH_COOKIE_KEYS)


def is_login_url(url: str) -> bool:
    """当前 URL 是否明确处于登录页或风控中转页。"""
    return bool(_LOGIN_URL_RE.search(url or ""))


def wait_until_stable(page, timeout: float = 8.0, settle: float = 1.2,
                      interval: float = 0.25) -> str:
    """等待 URL 稳定：在 settle 秒内不再变化，才认为跳转链已结束。

    这是修复误判的关键。domcontentloaded 只保证文档解析完成，
    京东会在此之后继续跳转（search -> cfe.m.jd.com 风控 -> passport 登录页），
    必须等跳转链走完再判定登录态，否则会停在中间页上误判。

    :return: 稳定后的最终 URL
    """
    deadline = time.time() + timeout
    last_url = page.url
    stable_since = time.time()

    while time.time() < deadline:
        page.wait_for_timeout(int(interval * 1000))
        current = page.url
        if current != last_url:
            last_url = current
            stable_since = time.time()
            continue
        if time.time() - stable_since >= settle:
            break

    try:
        page.wait_for_load_state("domcontentloaded", timeout=1500)
    except PWTimeout:
        # 页面可能仍在加载，但不影响 URL 判定
        pass
    return page.url


# --------------------------------------------------------------------------- #
# 登录态判定
# --------------------------------------------------------------------------- #
def check_login_status(page, context, settle: bool = False) -> tuple:
    """判断是否处于登录态。

    :param settle: 为 True 时先等待 URL 稳定再判定。仅应在刚触发过导航后使用，
                   不要放进轮询循环里（否则每轮都要空等 settle 秒）。
    :return: (是否已登录, 判定依据说明)
    """
    if settle:
        wait_until_stable(page)

    url = page.url or ""

    # 信号1（唯一可信）：登录凭证 Cookie
    names = cookie_names(context)
    if all(k in names for k in AUTH_COOKIE_KEYS):
        return True, "Cookie 凭证 pt_key+pt_pin 存在"

    # 信号2（锚点判定）：明确落在登录页/风控页 => 未登录。
    # 注意：不能反过来用「不在 passport 域 => 已登录」，因为 cfe.m.jd.com
    # 等中间跳转页同样不在 passport 域，那正是原代码误判的根源。
    if is_login_url(url):
        return False, f"当前处于登录页/风控页：{url}"

    # 信号3（辅助）：页面出现登录态元素，且必须同时具备辅助凭证
    if any(k in names for k in AUX_COOKIE_KEYS):
        try:
            if page.locator(LOGGED_IN_SELECTORS).first.is_visible():
                return True, "DOM 出现登录态元素且存在辅助凭证"
        except Exception:
            pass

    # 其余情况一律保守判为未登录：宁可多登一次，也不要误报成功
    return False, f"无有效登录凭证（URL={url}）"


def wait_login_success(page, context, timeout: int = 120) -> bool:
    """轮询等待登录成功。"""
    deadline = time.time() + timeout
    last_state = ""

    while time.time() < deadline:
        # 轮询里只做便宜判定，不用 settle（否则每轮都要空等）
        ok, _reason = check_login_status(page, context, settle=False)
        if ok:
            return True

        # 打印当前状态，方便观察卡在哪个环节
        state = f"url={page.url}"
        if state != last_state:
            print(f"[*] 等待登录中... {state}")
            last_state = state

        # 用 page.wait_for_timeout 而不是 time.sleep，
        # 让 Playwright 的事件循环正常处理页面事件
        page.wait_for_timeout(1000)

    return False


# --------------------------------------------------------------------------- #
# Cookie 读写（带校验）
# --------------------------------------------------------------------------- #
def save_cookies(context) -> tuple:
    """保存 Cookie 到本地。

    :return: (文件中的总条数, 其中的登录凭证条数)
             凭证条数为 0 说明本次登录态没有真正落盘，下次启动仍需重新登录。
    """
    cookies = context.cookies()
    COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    names = {c.get("name") for c in cookies}
    auth_count = sum(1 for k in AUTH_COOKIE_KEYS if k in names)
    print(f"[+] Cookie 已保存（{len(cookies)} 条，其中登录凭证 {auth_count} 条）")

    if auth_count < len(AUTH_COOKIE_KEYS):
        missing = [k for k in AUTH_COOKIE_KEYS if k not in names]
        print(f"[!] 警告：未捕获到登录凭证 {missing}，该存档下次启动将无法免登录")

    return len(cookies), auth_count


def load_cookies(context) -> bool:
    """把本地 Cookie 注入上下文。

    :return: 是否成功注入且确实带有登录凭证
    """
    if not COOKIE_FILE.exists():
        print(f"[*] 未找到 Cookie 文件：{COOKIE_FILE.name}")
        return False

    try:
        cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[!] Cookie 文件读取失败：{e}")
        return False

    if not isinstance(cookies, list) or not cookies:
        print("[!] Cookie 文件内容为空或格式不正确")
        return False

    context.add_cookies(cookies)

    # add_cookies 会静默丢弃无效条目（domain 不合法 / 字段缺失等），
    # 所以必须回读实际生效的条数，并确认凭证真的进去了
    effective = cookie_names(context)
    print(f"[+] 已加载本地 Cookie（文件 {len(cookies)} 条，"
          f"实际生效 {len(effective)} 条）")

    missing = [k for k in AUTH_COOKIE_KEYS if k not in effective]
    if missing:
        print(f"[!] 存档中缺少登录凭证 {missing}，判定该存档已失效")
        return False

    return True


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def login_jd(username: str = "", password: str = "", headless: bool = False):
    """登录京东并返回可用的浏览器上下文。

    :return: (playwright, browser, context, page)
             注意比 page 多了前三个对象，用完必须调用 cleanup(playwright, browser)
    """
    # 关键：用 start() 而不是 with 语句，Playwright 的生命周期
    # 交由调用方控制，函数返回时不会被自动 stop
    playwright = sync_playwright().start()
    browser = None

    try:
        browser = playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
        )
        page = context.new_page()

        # —— 第一步：尝试用本地 Cookie 免登录 ——
        if load_cookies(context):
            print("[*] 正在验证 Cookie 有效性...")
            page.goto(SEARCH_URL, wait_until="domcontentloaded")
            # 关键：等跳转链走完再判定，否则会停在 cfe.m.jd.com 风控中转页上误判
            ok, reason = check_login_status(page, context, settle=True)
            if ok:
                print(f"[✓] Cookie 有效，跳过登录（{reason}）")
                return playwright, browser, context, page
            print(f"[!] Cookie 已失效，需要重新登录（{reason}）")
            context.clear_cookies()

        # —— 第二步：打开登录页 ——
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        wait_until_stable(page)

        if username and password:
            try:
                page.click(".login-tab-r", timeout=5000)
            except PWTimeout:
                print("[*] 未找到账号登录页签，按当前表单继续")
            page.fill("#loginname", username)
            page.fill("#nloginpwd", password)
            page.click(".login-btn")
            print("[*] 已提交账号密码，等待验证...")
        else:
            qr = page.locator(".qrcode-img, .qrcode-login img").first
            try:
                if qr.is_visible():
                    qr.screenshot(path=str(BASE_DIR / "jd_qrcode.png"))
                    print("[*] 二维码已保存到 jd_qrcode.png")
            except Exception:
                pass
            print("[*] 请用京东 App 扫码，并在手机上点击【确认登录】")

        if not wait_login_success(page, context, timeout=120):
            try:
                page.screenshot(path=str(BASE_DIR / "login_failed.png"))
                print("[!] 登录超时，截图已保存到 login_failed.png")
            except Exception:
                pass
            raise TimeoutError("登录超时，截图已保存到 login_failed.png")

        print(f"[✓] 登录成功！当前 URL: {page.url}")
        # 等跳转链结束再落盘，否则可能存到跳转中途的 Cookie 快照，
        # 那正是存档里缺失 pt_key 的典型原因
        wait_until_stable(page)
        page.wait_for_timeout(1000)

        _total, auth_count = save_cookies(context)
        if auth_count < len(AUTH_COOKIE_KEYS):
            print("[!] 登录已成功但凭证未完整落盘，下次启动仍需重新登录")

        return playwright, browser, context, page

    except BaseException:
        # 出错时不要泄漏浏览器进程
        cleanup(playwright, browser)
        raise


def cleanup(playwright, browser):
    """统一释放资源：先关浏览器，再停 playwright"""
    try:
        if browser is not None:
            browser.close()
    finally:
        if playwright is not None:
            playwright.stop()


# ----------------- 使用示例 -----------------
if __name__ == "__main__":
    pw, browser, context, page = login_jd()

    try:
        # 现在 page 可以正常使用了
        page.goto("https://order.jd.com/center/list.action",
                  wait_until="domcontentloaded")
        wait_until_stable(page)
        print(f"[*] 当前页面：{page.title()}")

        ok, reason = check_login_status(page, context)
        print(f"[*] 登录态复核：{ok}（{reason}）")

        # ... 你的其他爬取逻辑 ...

    finally:
        # 全部用完后手动释放
        cleanup(pw, browser)
