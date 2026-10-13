import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

COOKIE_FILE = Path("jd_cookies.json")
LOGIN_URL = "https://passport.jd.com/new/login.aspx"
HOME_URL = "https://www.jd.com"
SEARCH_URL='https://search.jd.com/Search?keyword=&enc=utf-8'

def check_login_status(page, context) -> bool:
    """
    多信号联合判断登录状态，任一命中即认为已登录
    """
    # 信号1: Cookie（最可靠）—— 登录成功后京东会写入 pt_key / pt_pin
    cookie_names = {c["name"] for c in context.cookies()}
    if "pt_key" in cookie_names and "pt_pin" in cookie_names:
        return True

    # 信号2: URL 已离开 passport 登录域
    if "passport.jd.com" not in page.url:
        return True

    # 信号3: 页面 DOM 出现登录态元素（用户昵称）
    try:
        if page.locator(".nickname, .user_logout, #ttbar-login .nickname").first.is_visible():
            return True
    except Exception:
        pass

    return False


def wait_login_success(page, context, timeout: int = 120) -> bool:
    """轮询等待登录成功"""
    deadline = time.time() + timeout
    last_state = ""

    while time.time() < deadline:
        status = check_login_status(page, context)
        if status:
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


def save_cookies(context):
    cookies = context.cookies()
    COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
    print(f"[+] Cookie 已保存（{len(cookies)} 条）")


def load_cookies(context) -> bool:
    if not COOKIE_FILE.exists():
        return False
    context.add_cookies(json.loads(COOKIE_FILE.read_text()))
    print("[+] 已加载本地 Cookie")
    return True


def login_jd(username: str = "", password: str = "", headless: bool = False):
    """
    :return: (playwright, browser, context, page) 注意多了第一个 playwright 对象
    """
    # 关键修改：用 start() 而不是 with 语句，Playwright 的生命周期
    # 交由调用方控制，函数返回时不会被自动 stop
    playwright = sync_playwright().start()

    browser = playwright.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(
        viewport={"width": 1366, "height": 768},
        locale="zh-CN",
    )
    page = context.new_page()

    # —— 以下登录逻辑不变 ——
    if load_cookies(context):
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        if check_login_status(page, context):
            print("[✓] Cookie 有效，跳过登录")
            return playwright, browser, context, page
        print("[!] Cookie 已过期，需要重新登录")
        context.clear_cookies()

    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    if username and password:
        page.click(".login-tab-r")
        page.fill("#loginname", username)
        page.fill("#nloginpwd", password)
        page.click(".login-btn")
        print("[*] 已提交账号密码，等待验证...")
    else:
        qr = page.locator(".qrcode-img, .qrcode-login img").first
        if qr.is_visible():
            qr.screenshot(path="jd_qrcode.png")
            print("[*] 二维码已保存到 jd_qrcode.png")
        print("[*] 请用京东 App 扫码，并在手机上点击【确认登录】")

    if not wait_login_success(page, context, timeout=120):
        page.screenshot(path="login_failed.png")
        cleanup(playwright, browser)
        raise TimeoutError("登录超时，截图已保存到 login_failed.png")

    print(f"[✓] 登录成功！当前 URL: {page.url}")
    page.wait_for_timeout(3000)
    save_cookies(context)

    return playwright, browser, context, page


def cleanup(playwright, browser):
    """统一释放资源：先关浏览器，再停 playwright"""
    try:
        browser.close()
    finally:
        playwright.stop()


# ----------------- 使用示例 -----------------
if __name__ == "__main__":
    playwright, browser, context, page = login_jd()

    try:
        # 现在 page 可以正常使用了
        page.goto("https://order.jd.com/center/list.action")
        print(page.title())

        # ... 你的其他爬取逻辑 ...

    finally:
        # 全部用完后手动释放
        cleanup(playwright, browser)
