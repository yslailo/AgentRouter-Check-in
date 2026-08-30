#!/usr/bin/env python3

import os
import sys
import time
import random
import socket
import concurrent.futures

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ============================================================
# 环境变量配置
# ============================================================

USERNAME = os.getenv("USERNAME") or ""
PASSWORD = os.getenv("PASSWORD") or ""

# 多账号配置（可选）
# 多行文本，每行一个账号，格式：邮箱:密码
ACCOUNTS = os.getenv("ACCOUNTS") or ""

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN") or ""
TG_CHAT_ID = os.getenv("TG_CHAT_ID") or ""


# ============================================================
# 代理配置（可选）
# ============================================================

PROXY_SERVER = os.getenv("PROXY_SERVER") or ""
PROXY_USERNAME = os.getenv("PROXY_USERNAME") or ""
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD") or ""


# ============================================================
# 基础配置
# ============================================================

# 默认站点（ACCOUNTS 行中未写站点前缀时使用）
DEFAULT_SITE_URL = "https://agentrouter.org"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# ============================================================
# 代理池配置
# ============================================================

FREEPROXY_POOL_URL = (
    "https://charlespikachu.github.io/freeproxy/proxies.json"
)

PROXY_COUNTRY = os.getenv("PROXY_COUNTRY") or ""

PROXY_SAMPLE_SIZE = int(
    os.getenv("PROXY_SAMPLE_SIZE") or "100"
)

PROXY_MAX_ROUNDS = int(
    os.getenv("PROXY_MAX_ROUNDS") or "5"
)

PROBE_TIMEOUT = 15

PROBE_WORKERS = 50

MAX_LOGIN_ATTEMPTS = 3


# ============================================================
# 浏览器请求头
# ============================================================

BROWSER_HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "sec-ch-ua": (
        '"Not=A?Brand";v="99", '
        '"Microsoft Edge";v="151", '
        '"Chromium";v="151"'
    ),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "upgrade-insecure-requests": "1",
    "user-agent": USER_AGENT,
}


# ============================================================
# 代理池请求头
# ============================================================

POOL_HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "referer": "https://charlespikachu.github.io/freeproxy/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": USER_AGENT,
}


# ============================================================
# WAF / 人机验证特征
# ============================================================

WAF_MARKERS = (
    # 指纹级特征
    "aliyun_waf_aa",
    "aliyun_waf_bb",
    "initaliyuncaptcha",
    "aliyuncaptcha-sliding-slider",
    "cf_app_waf",
    "captcha-frontend",

    # Cookie / WAF 标识
    "acw_sc__v2",
    "__acw",
    "acw_tc",
    "cdn_sec_tc",
    "x5secdata",
    "punishpage",

    # 中文验证
    "访问验证",
    "滑动验证",
    "安全验证",
    "人机验证",
    "滑动滑块",
    "拖动滑块",
    "完成以下验证",
    "请完成验证",
    "验证成功后",
    "异常访问拦截",
    "请求被拦截",

    # 通用验证
    "verify you are human",
    "verifying you are human",
    "attention required",
    "just a moment",
    "challenge-platform",
    "turnstile",
    "cf-challenge",
    "unusual traffic",
    "captcha",
)


# ============================================================
# 日志
# ============================================================

def log(message: str):
    """
    极简日志。

    注意：
    不允许传入用户名、User ID、密码、代理 IP、
    URL、HTML、接口响应、异常详情等敏感信息。
    """
    print(message, flush=True)


# ============================================================
# Telegram
# ============================================================

def send_telegram(message: str) -> bool:
    """
    发送 Telegram 消息。

    消息内容只允许包含：
    - 当前余额
    - 签到状态

    不发送账号、用户名、User ID 等用户信息。
    """

    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False

    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"

        data = {
            "chat_id": TG_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }

        response = requests.post(
            url,
            json=data,
            timeout=30,
        )

        response.raise_for_status()

        return True

    except Exception:
        # 故意不打印异常，避免异常中包含敏感信息
        return False


# ============================================================
# 账号解析
# ============================================================

def parse_accounts() -> list:
    """
    解析账号列表。

    优先级：
    1. ACCOUNTS（多行，每行 [站点URL|]邮箱:密码）
    2. USERNAME / PASSWORD（旧版单账号兼容）

    说明：
    - 站点前缀可省略，省略时使用默认站点
    - 站点与账号用 | 分隔
    - 密码中可以包含冒号，
      只按第一个冒号分割邮箱与密码
    """

    accounts = []

    if ACCOUNTS:

        for raw_line in ACCOUNTS.splitlines():

            line = raw_line.strip()

            if not line:
                continue

            site_url = ""

            body = line

            # 站点前缀
            if "|" in line:

                site_url, _, body = line.partition("|")

                site_url = site_url.strip()

            if ":" not in body:
                continue

            username, _, password = body.partition(":")

            username = username.strip()

            password = password.strip()

            if not username or not password:
                continue

            # 站点归一化
            if not site_url:
                site_url = DEFAULT_SITE_URL

            elif not site_url.startswith("http"):
                site_url = f"https://{site_url}"

            site_url = site_url.rstrip("/")

            accounts.append(
                {
                    "site": site_url,
                    "username": username,
                    "password": password,
                }
            )

    # 兼容旧单账号配置
    if not accounts and USERNAME and PASSWORD:

        accounts.append(
            {
                "site": DEFAULT_SITE_URL,
                "username": USERNAME,
                "password": PASSWORD,
            }
        )

    return accounts


# ============================================================
# 站点打码
# ============================================================

def mask_site(
    site_url: str,
) -> str:

    """
    站点域名打码。

    只保留域名前几位，其余屏蔽。
    例：https://agentrouter.org -> agen***
    """

    domain = site_url or ""

    for prefix in ("https://", "http://"):

        if domain.startswith(prefix):

            domain = domain[len(prefix):]

            break

    domain = domain.split("/", 1)[0]

    visible = domain[:4]

    return f"{visible}***"


# ============================================================
# 获取代理池
# ============================================================

def fetch_proxy_pool() -> list:
    """
    从 freeproxy 获取代理池。
    """

    response = requests.get(
        FREEPROXY_POOL_URL,
        headers=POOL_HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    payload = response.json()

    if isinstance(payload, dict):
        items = payload.get("data") or []

    elif isinstance(payload, list):
        items = payload

    else:
        items = []

    if not items:
        raise Exception("代理池为空")

    return items


# ============================================================
# 构造代理地址
# ============================================================

def build_proxy_candidates(
    ip,
    port,
    protocol=""
) -> list:

    protocol = str(protocol or "").lower()

    base = f"{ip}:{int(port)}"

    tokens = {
        token.strip()
        for token in protocol.replace("/", ",").split(",")
        if token.strip()
    }

    if "http" in tokens:
        return [
            (
                "http",
                f"http://{base}"
            )
        ]

    if "https" in tokens:
        return [
            (
                "https",
                f"https://{base}"
            ),
            (
                "http",
                f"http://{base}"
            ),
        ]

    if "socks5" in tokens:
        return [
            (
                "socks5",
                f"socks5://{base}"
            )
        ]

    if "socks4" in tokens:
        return [
            (
                "socks4",
                f"socks4://{base}"
            )
        ]

    return [
        (
            "http",
            f"http://{base}"
        )
    ]


# ============================================================
# TCP 预检
# ============================================================

def _tcp_reachable(
    ip,
    port,
    timeout: float = 5
) -> bool:

    try:
        with socket.create_connection(
            (
                str(ip),
                int(port)
            ),
            timeout=timeout,
        ):
            return True

    except OSError:
        return False


# ============================================================
# WAF 判断
# ============================================================

def looks_like_waf(
    status_code: int,
    html: str
) -> bool:

    if status_code in (403, 405, 429):
        return True

    body = (html or "").lower()

    return any(
        marker.lower() in body
        for marker in WAF_MARKERS
    )


# ============================================================
# 探测代理
# ============================================================

def probe_proxy(
    proxy_item: dict,
    site_url: str,
):

    ip = proxy_item.get("ip")
    port = proxy_item.get("port")

    try:
        candidates = build_proxy_candidates(
            ip,
            port,
            proxy_item.get("protocol"),
        )

    except (TypeError, ValueError):
        return None, "BAD_FORMAT"

    # TCP 预检
    if not _tcp_reachable(ip, port):
        return None, "PORT_UNREACHABLE"

    for scheme, server in candidates:

        try:

            response = requests.get(
                f"{site_url}/login",
                headers=BROWSER_HEADERS,
                proxies={
                    "http": server,
                    "https": server,
                },
                timeout=PROBE_TIMEOUT,
                allow_redirects=True,
            )

            # WAF
            if looks_like_waf(
                response.status_code,
                response.text[:20000],
            ):
                return None, "WAF"

            # 非 200
            if response.status_code != 200:
                return None, f"HTTP_{response.status_code}"

            return {
                "ip": ip,
                "port": int(port),
                "protocol": proxy_item.get(
                    "protocol",
                    ""
                ),
                "scheme": scheme,
                "anonymity": proxy_item.get(
                    "anonymity",
                    ""
                ),
                "speed": proxy_item.get(
                    "speed",
                    0
                ),
                "server": server,
            }, None

        except requests.exceptions.ProxyError:

            # 协议不匹配，继续尝试下一个 scheme
            continue

        except Exception:

            # 不返回异常文本
            return None, "ERROR"

    return None, "PROXY_ERROR"


# ============================================================
# 批量探测
# ============================================================

def _probe_batch(
    batch: list,
    site_url: str,
) -> tuple:

    clean = []

    stats = {}

    if not batch:
        return clean, stats

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(
            PROBE_WORKERS,
            len(batch),
        )
    ) as executor:

        future_map = {
            executor.submit(
                probe_proxy,
                item,
                site_url,
            ): item
            for item in batch
        }

        for future in concurrent.futures.as_completed(
            future_map
        ):

            try:
                result, reason = future.result()

            except Exception:
                result = None
                reason = "ERROR"

            if result is not None:

                clean.append(result)

            else:

                stats[reason] = (
                    stats.get(reason, 0) + 1
                )

    return clean, stats


# ============================================================
# 查找干净代理
# ============================================================

def find_clean_proxies(
    site_url: str,
    sample_size: int = PROXY_SAMPLE_SIZE,
    max_rounds: int = PROXY_MAX_ROUNDS,
    needed_count: int = 0,
) -> list:

    pool = fetch_proxy_pool()

    # 国家过滤
    if PROXY_COUNTRY:

        pool = [
            item
            for item in pool
            if str(
                item.get("country", "")
            ).upper()
            == PROXY_COUNTRY
        ]

        if not pool:
            raise Exception("代理池过滤后为空")

    # 打乱代理
    random.shuffle(pool)

    remaining = pool

    needed = needed_count or max(
        1,
        MAX_LOGIN_ATTEMPTS,
    )

    clean_proxies = []

    stats = {}

    scanned = 0

    for round_no in range(
        1,
        max_rounds + 1,
    ):

        batch = remaining[:sample_size]

        remaining = remaining[sample_size:]

        if not batch:
            break

        round_clean, round_stats = _probe_batch(
            batch,
            site_url,
        )

        clean_proxies.extend(
            round_clean
        )

        scanned += len(batch)

        for key, value in round_stats.items():

            stats[key] = (
                stats.get(key, 0)
                + value
            )

        if len(clean_proxies) >= needed:
            break

    # 按代理池速度排序
    clean_proxies.sort(
        key=lambda item: (
            item.get("speed") or 99999
        )
    )

    return clean_proxies


# ============================================================
# 共享代理队列（按站点独立）
# ============================================================

CLEAN_PROXIES_BY_SITE = {}

POOL_BROKEN_SITES = set()


def pop_proxy(
    site_url: str,
    total_accounts: int,
) -> dict | None:

    """
    从该站点的共享代理队列取一个干净代理。

    队列耗尽时针对该站点重新探测，
    探测失败则本次运行不再尝试该站点的代理池。
    """

    if site_url in POOL_BROKEN_SITES:
        return None

    queue = CLEAN_PROXIES_BY_SITE.get(
        site_url
    ) or []

    if not queue:

        try:

            needed = max(
                1,
                MAX_LOGIN_ATTEMPTS * total_accounts,
            )

            queue = find_clean_proxies(
                site_url=site_url,
                needed_count=needed,
            )

        except Exception:

            # 不打印异常详情
            queue = []

        CLEAN_PROXIES_BY_SITE[site_url] = queue

        if not queue:

            POOL_BROKEN_SITES.add(site_url)

            return None

    return queue.pop(0)


# ============================================================
# 等待 WAF
# ============================================================

def wait_for_waf_ready(
    page,
    context=None,
    timeout_ms: int = 45000,
) -> bool:

    WAF_COOKIE_NAMES = (
        "acw_tc",
        "cdn_sec_tc",
        "acw_sc__v2",
        "__jsluid_s",
        "__cf_bm",
    )

    deadline = (
        time.time()
        + timeout_ms / 1000.0
    )

    reload_count = 0

    last_reload_time = 0.0

    while time.time() < deadline:

        try:

            state = page.evaluate(
                """
                () => {
                    const bodyText =
                        (document.body &&
                         document.body.innerText) || '';

                    const hasInputs =
                        document.querySelectorAll(
                            'input'
                        ).length > 0;

                    const hasLoginButton =
                        /Sign in with Email or Username|Log In|登录/
                        .test(bodyText);

                    const challengeSelectors = [
                        '#challenge-form',
                        '.cf-challenge',
                        '#cf-challenge-running',
                        'iframe[src*="challenges.cloudflare.com"]',
                        '[class*="turnstile"]',
                        '[class*="captcha"]'
                    ];

                    const isChallenge =
                        challengeSelectors.some(
                            sel => !!document.querySelector(sel)
                        )
                        ||
                        /challenge|verify you are human|attention required|verification/i
                        .test(bodyText.slice(0, 300))
                        ||
                        document.title
                            .toLowerCase()
                            .includes('verification');

                    return {
                        hasInputs,
                        hasLoginButton,
                        isChallenge
                    };
                }
                """
            )

        except Exception:

            page.wait_for_timeout(1000)

            continue

        # 登录页面已经出现
        if (
            state.get("hasInputs")
            or state.get("hasLoginButton")
        ):
            return True

        # WAF 挑战
        if state.get("isChallenge"):

            # Cookie 已生成时刷新
            if (
                context is not None
                and reload_count < 3
                and (
                    time.time()
                    - last_reload_time
                    >= 10
                )
            ):

                try:

                    cookie_names = {
                        cookie.get("name")
                        for cookie in context.cookies()
                    }

                    if any(
                        name in cookie_names
                        for name in WAF_COOKIE_NAMES
                    ):

                        page.reload(
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )

                        reload_count += 1

                        last_reload_time = time.time()

                except Exception:
                    pass

        page.wait_for_timeout(1000)

    return False


# ============================================================
# 点击邮箱 / 用户名登录
# ============================================================

def click_email_login_button(
    page,
    timeout_ms: int = 15000,
) -> bool:

    # 优先 Playwright 定位
    try:

        target = page.get_by_text(
            "Sign in with Email or Username"
        )

        target.first.wait_for(
            state="visible",
            timeout=timeout_ms,
        )

        target.first.click(
            timeout=10000
        )

        return True

    except Exception:
        pass

    # JS 回退
    try:

        clicked = page.evaluate(
            """
            () => {
                const candidates =
                    Array.from(
                        document.querySelectorAll(
                            'button, [role="tab"], a, span, div'
                        )
                    );

                const target =
                    candidates.find(el => {
                        const text =
                            (el.innerText || '').trim();

                        return (
                            text ===
                            'Sign in with Email or Username'
                            ||
                            text.includes(
                                'Email or Username'
                            )
                            ||
                            text ===
                            'Sign in with Email'
                        );
                    });

                if (target) {
                    target.click();
                    return true;
                }

                return false;
            }
            """
        )

        if clicked:
            return True

    except Exception:
        pass

    return False


# ============================================================
# 浏览器登录
# ============================================================

def browser_login_complete(
    account: dict,
    proxy: dict | None = None,
) -> dict | None:

    # --------------------------------------------------------
    # 代理配置
    # --------------------------------------------------------

    proxy_config = None

    if proxy and proxy.get("server"):

        proxy_config = {
            "server": proxy["server"]
        }

    elif PROXY_SERVER:

        proxy_config = {
            "server": PROXY_SERVER
        }

        if PROXY_USERNAME and PROXY_PASSWORD:

            proxy_config["username"] = (
                PROXY_USERNAME
            )

            proxy_config["password"] = (
                PROXY_PASSWORD
            )

    # --------------------------------------------------------
    # 启动浏览器
    # --------------------------------------------------------

    result = None

    with sync_playwright() as p:

        browser = p.chromium.launch(

            headless=True,

            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],

            proxy=proxy_config,
        )

        context = browser.new_context(

            viewport={
                "width": 1920,
                "height": 1080,
            },

            user_agent=USER_AGENT,
        )

        page = context.new_page()

        try:

            # ------------------------------------------------
            # Step 1：访问登录页
            # ------------------------------------------------

            page.goto(
                f"{account['site']}/login",
                wait_until="domcontentloaded",
                timeout=45000,
            )

            # ------------------------------------------------
            # Step 2：等待 WAF
            # ------------------------------------------------

            wait_for_waf_ready(
                page,
                context=context,
                timeout_ms=45000,
            )

            # ------------------------------------------------
            # 检查页面是否有输入框
            # ------------------------------------------------

            has_inputs = page.locator(
                "input"
            ).count()

            # ------------------------------------------------
            # 没有输入框时切换登录方式
            # ------------------------------------------------

            if has_inputs == 0:

                clicked = click_email_login_button(
                    page
                )

                if not clicked:

                    # 不输出页面文本、HTML、URL
                    raise Exception(
                        "登录表单未出现"
                    )

                page.wait_for_timeout(
                    1500
                )

            # ------------------------------------------------
            # Step 3：等待表单
            # ------------------------------------------------

            wait_result = page.evaluate(
                """
                async () => {

                    let attempts = 0;

                    const maxAttempts = 30;

                    while (
                        attempts < maxAttempts
                    ) {

                        const username =
                            document.querySelector(
                                'input#username'
                            );

                        const password =
                            document.querySelector(
                                'input#password'
                            );

                        const submit =
                            document.querySelector(
                                'button[type="submit"]'
                            );

                        if (
                            username &&
                            password &&
                            submit &&
                            username.offsetParent !== null &&
                            password.offsetParent !== null &&
                            submit.offsetParent !== null
                        ) {

                            return {
                                success: true
                            };
                        }

                        await new Promise(
                            resolve =>
                                setTimeout(
                                    resolve,
                                    500
                                )
                        );

                        attempts++;
                    }

                    return {
                        success: false
                    };
                }
                """
            )

            if not wait_result.get(
                "success"
            ):

                raise Exception(
                    "登录表单未出现"
                )

            # ------------------------------------------------
            # 填写用户名
            # ------------------------------------------------

            username_locator = page.locator(
                "input#username"
            )

            username_locator.wait_for(
                state="visible",
                timeout=5000,
            )

            username_locator.click(
                timeout=5000
            )

            username_locator.fill(
                account["username"],
                timeout=5000,
            )

            # ------------------------------------------------
            # 填写密码
            # ------------------------------------------------

            password_locator = page.locator(
                "input#password"
            )

            password_locator.wait_for(
                state="visible",
                timeout=5000,
            )

            password_locator.click(
                timeout=5000
            )

            password_locator.fill(
                account["password"],
                timeout=5000,
            )

            page.wait_for_timeout(
                1000
            )

            # ------------------------------------------------
            # Step 4：提交
            # ------------------------------------------------

            submit_locator = page.locator(
                'button[type="submit"]'
            )

            submit_locator.wait_for(
                state="visible",
                timeout=5000,
            )

            submit_locator.click(
                timeout=5000
            )

            # ------------------------------------------------
            # Step 5：等待登录
            # ------------------------------------------------

            page.wait_for_timeout(
                3000
            )

            # 检查验证码
            has_captcha = page.evaluate(
                """
                () => !!document.querySelector(
                    '#nc_1_n1z, .nc-container, [class*="captcha"]'
                )
                """
            )

            if has_captcha:

                page.wait_for_timeout(
                    5000
                )

            # ------------------------------------------------
            # Step 6：获取用户信息
            # ------------------------------------------------

            api_result = page.evaluate(
                """
                async () => {

                    try {

                        let userStr = null;

                        for (
                            let attempt = 0;
                            attempt < 10;
                            attempt++
                        ) {

                            userStr =
                                localStorage.getItem(
                                    'user'
                                );

                            if (userStr) {
                                break;
                            }

                            await new Promise(
                                resolve =>
                                    setTimeout(
                                        resolve,
                                        500
                                    )
                            );
                        }

                        if (!userStr) {

                            return {
                                success: false,
                                error: "USER_NOT_FOUND"
                            };
                        }

                        const localUser =
                            JSON.parse(
                                userStr
                            );

                        if (!localUser.id) {

                            return {
                                success: false,
                                error: "USER_ID_INVALID"
                            };
                        }

                        const response =
                            await fetch(
                                '/api/user/self',
                                {
                                    method: 'GET',

                                    headers: {
                                        'Accept':
                                            'application/json, text/plain, */*',

                                        'New-API-User':
                                            String(
                                                localUser.id
                                            )
                                    },

                                    credentials:
                                        'include',

                                    cache:
                                        'no-store'
                                }
                            );

                        let payload;

                        try {

                            payload =
                                await response.json();

                        } catch (err) {

                            return {
                                success: false,
                                status:
                                    response.status,
                                error:
                                    "INVALID_JSON"
                            };
                        }

                        return {
                            success:
                                response.ok,

                            status:
                                response.status,

                            payload
                        };

                    } catch (err) {

                        return {
                            success: false,
                            error: "API_ERROR"
                        };
                    }
                }
                """
            )

            # ------------------------------------------------
            # API 请求失败
            # ------------------------------------------------

            if not api_result.get(
                "success"
            ):

                raise Exception(
                    "获取余额失败"
                )

            payload = api_result.get(
                "payload"
            )

            if (
                not isinstance(
                    payload,
                    dict
                )
                or payload.get(
                    "success"
                ) is not True
            ):

                raise Exception(
                    "余额接口返回失败"
                )

            user_data = payload.get(
                "data"
            )

            if not isinstance(
                user_data,
                dict
            ):

                raise Exception(
                    "余额数据无效"
                )

            # ------------------------------------------------
            # 只读取 quota
            # ------------------------------------------------

            quota = user_data.get(
                "quota"
            )

            if (
                isinstance(quota, bool)
                or not isinstance(
                    quota,
                    (int, float)
                )
            ):

                raise Exception(
                    "余额数据无效"
                )

            # ------------------------------------------------
            # 只返回 quota
            #
            # 不再返回：
            # user_id
            # username
            # 账户
            # 其他用户资料
            # ------------------------------------------------

            result = {
                "quota": quota
            }

        except PlaywrightTimeoutError:

            # 不打印 URL、页面信息、异常内容
            result = None

        except Exception:

            # 不打印异常
            result = None

        finally:

            try:
                browser.close()
            except Exception:
                pass

    return result


# ============================================================
# 余额转换
# ============================================================

def format_balance(
    quota: int
) -> str:

    """
    quota 转换为美元。

    假设：
    500000 quota = $1
    """

    if quota is None:
        return "N/A"

    balance = quota / 500000

    return f"{balance:.2f}$"


# ============================================================
# 单账号签到
# ============================================================

def checkin_account(
    account: dict,
    total_accounts: int,
) -> dict | None:

    # --------------------------------------------------------
    # 通道一：
    # 自有代理
    # --------------------------------------------------------

    if PROXY_SERVER:

        result = browser_login_complete(
            account
        )

        if result:
            return result

    # --------------------------------------------------------
    # 通道二：
    # 免费代理池（按站点独立队列，耗尽自动重探）
    # --------------------------------------------------------

    for _ in range(
        MAX_LOGIN_ATTEMPTS
    ):

        proxy = pop_proxy(
            account["site"],
            total_accounts,
        )

        if proxy is None:
            break

        attempt = browser_login_complete(
            account,
            proxy,
        )

        if attempt:
            return attempt

    return None


# ============================================================
# 主签到逻辑
# ============================================================

def run_checkin():

    # --------------------------------------------------------
    # 解析账号
    # --------------------------------------------------------

    accounts = parse_accounts()

    if not accounts:

        # 不打印具体账号
        log("配置错误：登录凭据未配置")

        sys.exit(1)

    total = len(accounts)

    log(f"账号数量: {total}")

    # --------------------------------------------------------
    # 逐账号签到
    # --------------------------------------------------------

    results = []

    success_count = 0

    # 每个站点的账号计数（用于标签序号）
    site_counters = {}

    for account in accounts:

        site = account["site"]

        site_counters[site] = (
            site_counters.get(site, 0) + 1
        )

        label = (
            f"{mask_site(site)} "
            f"账号{site_counters[site]}"
        )

        login_result = checkin_account(
            account,
            total,
        )

        # ----------------------------------------------------
        # 单账号失败，不影响后续账号
        # ----------------------------------------------------

        if not login_result:

            log(f"{label}: 签到失败")

            results.append(
                (label, None)
            )

            continue

        # ----------------------------------------------------
        # 获取余额
        # ----------------------------------------------------

        balance = format_balance(
            login_result.get(
                "quota",
                0
            )
        )

        # ----------------------------------------------------
        # 只打印余额
        # ----------------------------------------------------

        log(f"{label}: 当前余额 {balance}")

        results.append(
            (label, balance)
        )

        success_count += 1

    # --------------------------------------------------------
    # 汇总 Telegram 通知
    # --------------------------------------------------------

    message_lines = [
        "🎁 <b>自动签到通知</b>",
        "",
    ]

    for label, balance in results:

        if balance is None:

            message_lines.append(
                f"❌ {label}: 签到失败"
            )

        else:

            message_lines.append(
                f"✅ {label}: 余额 {balance}"
            )

    message_lines.append("")

    message_lines.append(
        f"📊 成功 {success_count}/{total}"
    )

    send_telegram(
        "\n".join(message_lines)
    )

    # --------------------------------------------------------
    # 任一账号失败则退出码 1
    # --------------------------------------------------------

    if success_count < total:

        log("部分账号签到失败")

        sys.exit(1)

    log("全部账号签到成功")


# ============================================================
# Main
# ============================================================

def main():

    try:

        run_checkin()

    except KeyboardInterrupt:

        # 不打印任何敏感信息
        sys.exit(130)

    except Exception:

        # 不打印 traceback
        # 不打印 exception 内容
        log("脚本执行失败")

        send_telegram(
            "❌ <b>签到脚本执行失败</b>"
        )

        sys.exit(1)


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    main()
