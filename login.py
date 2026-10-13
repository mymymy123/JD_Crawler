import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

COOKIE_FILE = Path("jd_cookies.json")
LOGIN_URL = "https://passport.jd.com/new/login.aspx"
HOME_URL = "https://www.jd.com"
SEARCH_URL='https://search.jd.com/Search?keyword=&enc=utf-8'


PROXY = None

def check_login_status(page, context) -> bool:
    """
    多信号联合判断登录状态，任一命中即认为已登录
    """
    if any(m in page.url for m in ("passport.jd.com", "/login", "risk_handler")):
        return False

    cookie_names = {c["name"] for c in context.cookies()}
    if "pt_key" in cookie_names and "pt_pin" in cookie_names:
        return True

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


def login_jd(username: str = "", password: str = "", headless: bool = False,
             proxy: str = PROXY):
    """
    :param proxy: 出口代理，默认取模块常量 PROXY；传 None 或空串表示直连
    :return: (playwright, browser, context, page)
    """
    playwright = sync_playwright().start()

    browser = playwright.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        proxy={"server": proxy} if proxy else None,
    )
    context = browser.new_context(
        viewport={"width": 1366, "height": 768},
        locale="zh-CN",
    )
    page = context.new_page()

    if load_cookies(context):
        page.goto(SEARCH_URL, wait_until='networkidle')
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
    """统一释放资源：先关浏览器，再停 playwright。
    """
    if browser is not None:
        try:
            browser.close()
            print("[+] 浏览器已关闭")
        except Exception as e:
            print(f"[!] 关闭浏览器出错（已忽略）: {e}")

    if playwright is not None:
        try:
            playwright.stop()
            print("[+] Playwright 已停止")
        except Exception as e:
            print(f"[!] 停止 Playwright 出错（已忽略）: {e}")


# ----------------- 使用示例 -----------------
if __name__ == "__main__":
    playwright, browser, context, page = login_jd()

    try:
        page.goto("https://order.jd.com/center/list.action")
        print(page.title())
    finally:
        cleanup(playwright, browser)
