#!/usr/bin/env python3

import os
import sys
import time
import random
import socket
import traceback
import concurrent.futures
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# 环境变量配置
USERNAME = os.getenv("USERNAME") or ""
PASSWORD = os.getenv("PASSWORD") or ""
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN") or ""
TG_CHAT_ID = os.getenv("TG_CHAT_ID") or ""

# 代理配置（可选）
PROXY_SERVER = os.getenv("PROXY_SERVER") or ""  # 格式: http://host:port 或 socks5://host:port
PROXY_USERNAME = os.getenv("PROXY_USERNAME") or ""  # 代理用户名（如果需要）
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD") or ""  # 代理密码（如果需要）

SITE_URL = "https://agentrouter.org"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

# 代理池配置
FREEPROXY_POOL_URL = "https://charlespikachu.github.io/freeproxy/proxies.json"
# 不限国家，全池随机抽取（设为 CN 可只挑中国代理）
PROXY_COUNTRY = os.getenv("PROXY_COUNTRY") or ""
PROXY_SAMPLE_SIZE = int(os.getenv("PROXY_SAMPLE_SIZE") or "100")   # 每轮随机抽取数量
PROXY_MAX_ROUNDS = int(os.getenv("PROXY_MAX_ROUNDS") or "5")       # 最多扫描轮数（5 轮 = 500 个）
PROXY_TEST_URL = f"{SITE_URL}/login"   # 探测目标与实际登录页保持一致
PROBE_TIMEOUT = 15              # 单个代理探测超时（秒）
PROBE_WORKERS = 50              # 并发探测线程数
MAX_LOGIN_ATTEMPTS = 3          # 登录最多尝试的干净代理个数

# 探测时使用的浏览器请求头
BROWSER_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "sec-ch-ua": '"Not=A?Brand";v="99", "Microsoft Edge";v="151", "Chromium";v="151"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "upgrade-insecure-requests": "1",
    "user-agent": USER_AGENT,
}

# 拉取代理池时的请求头（防盗链，需要 referer）
POOL_HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "referer": "https://charlespikachu.github.io/freeproxy/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": USER_AGENT,
}

# 阿里云 WAF / 人机验证页特征（来自真实拦截样本实证）
WAF_MARKERS = (
    # ===== 指纹级特征（被拦页独有，判定可信度最高）=====
    "aliyun_waf_aa",                      # <meta name="aliyun_waf_aa">
    "aliyun_waf_bb",                      # <meta name="aliyun_waf_bb">
    "initaliyuncaptcha",                  # initAliyunCaptcha(...)
    "aliyuncaptcha-sliding-slider",       # 滑块容器 id
    "cf_app_waf",                         # appkey: "CF_APP_WAF"
    "captcha-frontend",                   # o.alicdn.com/captcha-frontend/...
    # ===== 阿里云 WAF 常规标识（挑战脚本写入的 cookie 名等）=====
    "acw_sc__v2", "__acw", "acw_tc", "cdn_sec_tc", "x5secdata", "punishpage",
    # ===== 页面中的中文验证说明 =====
    "访问验证", "滑动验证", "安全验证", "人机验证", "滑动滑块", "拖动滑块",
    "完成以下验证", "请完成验证", "验证成功后", "异常访问拦截", "请求被拦截",
    # ===== 通用人机校验字样（其他厂商兜底）=====
    "verify you are human", "verifying you are human", "attention required",
    "just a moment", "challenge-platform", "turnstile", "cf-challenge",
    "unusual traffic", "captcha",
)

def log(level: str, msg: str):
    """带时间戳的日志输出"""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def send_telegram(message: str) -> bool:
    """发送 Telegram 消息"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("WARN", "Telegram 配置不完整，跳过发送")
        print(f"--- 消息内容 ---\n{message}\n---------------")
        return False

    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TG_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }
        resp = requests.post(url, json=data, timeout=30)
        resp.raise_for_status()
        log("INFO", "Telegram 消息发送成功")
        return True
    except Exception as e:
        log("ERROR", f"Telegram 发送失败: {e}")
        return False

def fetch_proxy_pool() -> list:
    """从 freeproxy 拉取代理池（带防盗链 referer）"""
    resp = requests.get(FREEPROXY_POOL_URL, headers=POOL_HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    # 兼容 data 包装与纯列表两种格式
    if isinstance(payload, dict):
        items = payload.get("data") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    if not items:
        raise Exception("代理池返回为空或格式异常")
    return items


def mask_proxy(ip, port="") -> str:
    """日志中隐藏代理 IP（只保留前两段），避免泄露可用节点"""
    parts = str(ip).split(".")
    if len(parts) == 4:
        shown = ".".join(parts[:2]) + ".***.***"
    else:
        shown = str(ip)[:8] + "***"
    return f"{shown}:{port}" if str(port) else shown


def build_proxy_candidates(ip, port, protocol="") -> list:
    """
    按池子的协议字段生成候选代理地址（按优先级排序）。
    返回 [(scheme, url), ...]

    说明:
      - "Http"/"Https" 标签描述的是代理能否转发到对应目标，多数条目用
        http:// 走 CONNECT 隧道即可（目标站流量依旧端到端 TLS 加密）
      - "Http, Https" 组合条目 -> 只需 [("http", ...)]
      - 纯 "Https" 条目存在歧义: 少数代理要求客户端与代理之间先建立 TLS，
        先试 https:// 代理地址，协议不匹配(ProxyError)时自动降级 http:// 重试
      - Socks5/Socks4 -> 对应 socks scheme
    """
    protocol = str(protocol or "").lower()
    base = f"{ip}:{int(port)}"
    # 分词匹配，避免 "https" 的子串含 "http" 导致误判
    tokens = {t.strip() for t in protocol.replace("/", ",").split(",") if t.strip()}

    if "http" in tokens:
        # 标准代理（纯 Http 或 Http, Https 组合条目）
        return [("http", f"http://{base}")]
    if "https" in tokens:
        # 纯 Https: 先尝试 TLS 连代理，失败降级明文
        return [("https", f"https://{base}"), ("http", f"http://{base}")]
    if "socks5" in tokens:
        return [("socks5", f"socks5://{base}")]
    if "socks4" in tokens:
        return [("socks4", f"socks4://{base}")]
    return [("http", f"http://{base}")]


def _tcp_reachable(ip, port, timeout: float = 5) -> bool:
    """快速 TCP 握手预检端口是否开放（死 IP 直接淘汰，不耗完整超时）"""
    try:
        with socket.create_connection((str(ip), int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def looks_like_waf(status_code: int, html: str) -> bool:
    """
    判断响应是否为阿里云 WAF / 人机验证拦截页。

    注意: 阿里云滑块挑战页通常以 HTTP 200 返回（真实样本实证），
    所以内容指纹是主判定，状态码只是辅助信号。
    """
    if status_code in (403, 405, 429):
        return True
    body = (html or "").lower()
    return any(marker.lower() in body for marker in WAF_MARKERS)


def probe_proxy(proxy_item: dict):
    """
    检测单个代理：
      第一步: TCP 端口预检（5 秒）—— 代理可用性测试，打不通的 IP 直接判死
      第二步: 用该代理按候选协议访问 https://agentrouter.org/login（每个 15 秒），
              纯 Https 条目 TLS 握手失败时自动降级 http:// 重试
      第三步: 判定响应是否为阿里云 WAF 页面
    返回: 干净时 ({ip,port,protocol,scheme,speed,anonymity,server}, None)
          被淘汰/拦截时 (None, 失败原因 WAF/PORT_UNREACHABLE/...)
    """
    ip = proxy_item.get("ip")
    port = proxy_item.get("port")
    try:
        candidates = build_proxy_candidates(ip, port, proxy_item.get("protocol"))
    except (TypeError, ValueError):
        return None, "BAD_FORMAT"

    # 可用性预检：TCP 三次握手都完不成的 IP，不值得再发 HTTP
    if not _tcp_reachable(ip, port):
        return None, "PORT_UNREACHABLE"

    for scheme, server in candidates:
        try:
            resp = requests.get(
                PROXY_TEST_URL,
                headers=BROWSER_HEADERS,
                proxies={"http": server, "https": server},
                timeout=PROBE_TIMEOUT,
                allow_redirects=True,
            )
            if looks_like_waf(resp.status_code, resp.text[:20000]):
                return None, "WAF"
            if resp.status_code != 200:
                # 参考 rainyun 检测的保守策略: 非正常状态码一律不信任为干净 IP
                # （残缺代理返回的 5xx / 异常跳转页可能是污染响应）
                return None, f"HTTP_{resp.status_code}"
            return {
                "ip": ip,
                "port": int(port),
                "protocol": proxy_item.get("protocol", ""),
                "scheme": scheme,
                "anonymity": proxy_item.get("anonymity", ""),
                "speed": proxy_item.get("speed", 0),
                "server": server,
            }, None
        except requests.exceptions.ProxyError:
            # TCP 通但协议不匹配/拒绝 CONNECT —— 有备选 scheme 时降级重试
            continue
        except Exception as e:
            return None, type(e).__name__ or "ERROR"

    return None, "PROXY_ERROR"


def _probe_batch(batch: list) -> tuple:
    """并发探测一批代理，返回 (干净代理列表, 失败统计 dict)；不打印逐条日志"""
    clean = []
    stats = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(PROBE_WORKERS, len(batch))) as executor:
        future_map = {executor.submit(probe_proxy, item): item for item in batch}
        for future in concurrent.futures.as_completed(future_map):
            try:
                result, reason = future.result()
            except Exception:
                result, reason = None, "ERROR"

            if result is not None:
                clean.append(result)
            else:
                stats[reason] = stats.get(reason, 0) + 1

    return clean, stats


def find_clean_proxies(sample_size: int = PROXY_SAMPLE_SIZE, max_rounds: int = PROXY_MAX_ROUNDS) -> list:
    """
    扫描代理池：
      1. 拉取全量代理（默认不限国家；设置 PROXY_COUNTRY 环境变量可过滤）
      2. 整体洗牌后每轮随机切片 sample_size 个并发探测（不放回，不会重复测同一 IP）
      3. 累计凑够 MAX_LOGIN_ATTEMPTS 个干净 IP 即提前结束；最多 max_rounds 轮
      4. 干净代理按 pool 自带 speed 升序返回
    """
    pool = fetch_proxy_pool()

    if PROXY_COUNTRY:
        pool = [p for p in pool if str(p.get("country", "")).upper() == PROXY_COUNTRY]
        if not pool:
            raise Exception(f"代理池中不存在 country={PROXY_COUNTRY} 的代理")

    random.shuffle(pool)
    remaining = pool
    needed = max(1, MAX_LOGIN_ATTEMPTS)
    clean_proxies: list = []
    stats: dict = {}
    scanned = 0

    for round_no in range(1, max_rounds + 1):
        batch = remaining[:sample_size]
        remaining = remaining[sample_size:]
        if not batch:
            log("WARN", f"第 {round_no} 轮无剩余可测 IP，提前结束")
            break

        workers = min(PROBE_WORKERS, len(batch))
        log(
            "INFO",
            f"第 {round_no}/{max_rounds} 轮: 抽取 {len(batch)} 个（{workers} 线程并发）WAF 探测...",
        )
        round_clean, round_stats = _probe_batch(batch)
        clean_proxies.extend(round_clean)
        scanned += len(batch)
        for key, value in round_stats.items():
            stats[key] = stats.get(key, 0) + value

        if len(clean_proxies) >= needed:
            break
        if remaining and round_no < max_rounds:
            log("WARN", f"本轮结束累计可用 {len(clean_proxies)}/{needed} 个，换下一批继续...")

    other_errors = sum(v for k, v in stats.items() if k not in ("WAF", "PROXY_ERROR"))
    log(
        "INFO",
        f"探测完成: 可用 {len(clean_proxies)} 个"
        + f" | WAF {stats.get('WAF', 0)}"
        + f" | 连接失败 {stats.get('PROXY_ERROR', 0)}"
        + f" | 其他错误 {other_errors}"
        + f" | 共扫描 {scanned}/{len(pool)} 个",
    )

    # 按 pool 自带的响应速度升序，快的优先使用
    clean_proxies.sort(key=lambda p: p.get("speed") or 99999)
    return clean_proxies


def wait_for_waf_ready(page, context=None, timeout_ms: int = 45000) -> bool:
    """
    等待 WAF / 人机验证自动通过，直到出现真实登录界面。
    阿里云 WAF（acw_sc__v2 等）会在挑战页运行 JS 生成验证 Cookie，
    一旦 Cookie 生成而页面未自动跳转，则主动刷新进入真实页面。
    """
    WAF_COOKIE_NAMES = ("acw_tc", "cdn_sec_tc", "acw_sc__v2", "__jsluid_s", "__cf_bm")
    log("INFO", "检测 WAF / 人机验证状态...")
    deadline = time.time() + timeout_ms / 1000.0
    start_time = time.time()
    challenge_logged = False
    reload_count = 0
    last_reload_time = 0.0
    last_progress_log = 0

    while time.time() < deadline:
        try:
            state = page.evaluate("""
                () => {
                    const bodyText = (document.body && document.body.innerText) || '';
                    const hasInputs = document.querySelectorAll('input').length > 0;
                    const hasLoginButton = /Sign in with Email|Log In|登录/.test(bodyText);
                    const challengeSelectors = [
                        '#challenge-form', '.cf-challenge', '#cf-challenge-running',
                        'iframe[src*="challenges.cloudflare.com"]',
                        '[class*="turnstile"]', '[class*="captcha"]'
                    ];
                    const isChallenge = challengeSelectors.some(sel => !!document.querySelector(sel))
                        || /challenge|verify you are human|attention required|verification/i.test(bodyText.slice(0, 300))
                        || document.title.toLowerCase().includes('verification');
                    return {
                        hasInputs,
                        hasLoginButton,
                        isChallenge,
                        readyState: document.readyState,
                        url: location.href
                    };
                }
            """)
        except Exception:
            page.wait_for_timeout(1000)
            continue

        if state.get("hasInputs") or state.get("hasLoginButton"):
            log("INFO", "WAF 验证通过，登录界面已就绪")
            return True

        if state.get("isChallenge"):
            if not challenge_logged:
                log("WARN", "检测到人机验证/挑战页，等待其自动通过...")
                challenge_logged = True

            elapsed = int(time.time() - start_time)

            # WAF Cookie 已生成但页面仍停留在挑战页时，主动刷新进入真实页面
            if context is not None and reload_count < 3 and (time.time() - last_reload_time) >= 10:
                try:
                    cookie_names = {c.get("name") for c in context.cookies()}
                    if any(name in cookie_names for name in WAF_COOKIE_NAMES):
                        log("INFO", "WAF Cookie 已生成，刷新页面进入登录界面...")
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        reload_count += 1
                        last_reload_time = time.time()
                except Exception:
                    pass

            # 每 10 秒汇报一次进度，避免刷屏
            if elapsed >= 10 and elapsed - last_progress_log >= 10:
                log("INFO", f"  仍在等待 WAF 验证通过（已等待 {elapsed}s）...")
                last_progress_log = elapsed

        page.wait_for_timeout(1000)

    log("WARN", "等待 WAF 验证超时，继续尝试登录...")
    return False


def click_email_login_button(page, timeout_ms: int = 15000) -> bool:
    """
    点击 "Sign in with Email or Username" 切换按钮，
    点击后页面才会出现用户名/密码输入框。
    """
    log("INFO", "切换邮箱/用户名登录方式...")

    # 优先使用 Playwright 文本定位
    try:
        target = page.get_by_text("Sign in with Email or Username")
        target.first.wait_for(state="visible", timeout=timeout_ms)
        target.first.click(timeout=10000)
        log("INFO", "  ✓ 已点击邮箱/用户名登录按钮")
        return True
    except Exception:
        pass

    # 回退：通过 JS 在按钮/选项卡中查找并点击
    try:
        clicked = page.evaluate("""
            () => {
                const candidates = Array.from(
                    document.querySelectorAll('button, [role="tab"], a, span, div')
                );
                const target = candidates.find(el => {
                    const text = (el.innerText || '').trim();
                    return text === 'Sign in with Email or Username'
                        || text.includes('Email or Username')
                        || text === 'Sign in with Email';
                });
                if (target) {
                    target.click();
                    return true;
                }
                return false;
            }
        """)
        if clicked:
            log("INFO", "  ✓ 已通过脚本点击邮箱/用户名登录按钮")
            return True
    except Exception:
        pass

    log("WARN", "未找到邮箱/用户名登录按钮，可能表单已直接显示")
    return False


def browser_login_complete(proxy: dict | None = None) -> dict | None:
    """
    使用 Playwright 完成整个登录流程。
    proxy: 经 WAF 探测确认干净的代理，形如 {"server": "http://ip:port", ...}
           为空时回退到环境变量 PROXY_SERVER，再为空则直连。
    """
    # 配置代理：优先使用传入的代理池 IP
    proxy_config = None
    if proxy and proxy.get("server"):
        proxy_config = {"server": proxy["server"]}
        log("INFO", f"使用代理池干净 IP 登录: {mask_proxy(proxy['ip'], proxy['port'])}")
    elif PROXY_SERVER:
        proxy_config = {"server": PROXY_SERVER}
        if PROXY_USERNAME and PROXY_PASSWORD:
            proxy_config["username"] = PROXY_USERNAME
            proxy_config["password"] = PROXY_PASSWORD
        log("INFO", f"使用环境变量代理: {PROXY_SERVER}")
    else:
        log("INFO", "未提供可用代理，直连登录")

    log("INFO", f"使用浏览器自动化登录 {SITE_URL}...")

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
            proxy=proxy_config,  # 设置代理
        )

        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=USER_AGENT,
        )

        page = context.new_page()

        try:
            # Step 1: 访问登录页面
            log("INFO", "Step 1: 访问登录页面...")
            page.goto(f"{SITE_URL}/login", wait_until="domcontentloaded", timeout=45000)

            # Step 2: 等待 WAF / 人机验证自动通过，直到登录界面渲染完成
            log("INFO", "Step 2: 等待页面渲染及 WAF 验证通过...")
            wait_for_waf_ready(page, context=context, timeout_ms=45000)

            # 检查页面状态
            page_info = page.evaluate("""
                () => {
                    return {
                        url: window.location.href,
                        title: document.title,
                        readyState: document.readyState,
                        bodyText: document.body?.innerText?.substring(0, 300) || '',
                        hasInputs: document.querySelectorAll('input').length,
                        hasButtons: document.querySelectorAll('button').length,
                        hasForm: !!document.querySelector('form'),
                        htmlPreview: document.documentElement.outerHTML.substring(0, 500)
                    };
                }
            """)

            log("INFO", f"  当前 URL: {page_info.get('url')}")
            log("INFO", f"  页面标题: {page_info.get('title')}")
            log("INFO", f"  输入框数量: {page_info.get('hasInputs')}")
            log("INFO", f"  按钮数量: {page_info.get('hasButtons')}")

            # 如果页面没有输入框，尝试点击 "Sign in with Email or Username" 切换登录方式
            if page_info.get('hasInputs') == 0:
                if not click_email_login_button(page):
                    log("ERROR", f"页面没有输入框且未找到登录切换按钮！")
                    log("ERROR", f"页面文本预览: {page_info.get('bodyText')}")
                    log("ERROR", f"HTML 预览: {page_info.get('htmlPreview')}")

                    try:
                        screenshot_path = "page_error.png"
                        page.screenshot(path=screenshot_path, full_page=True)
                        log("INFO", f"已保存页面截图: {screenshot_path}")
                    except:
                        pass

                    raise Exception(f"登录页面加载异常，没有找到表单元素")

                # 点击切换后等待表单出现
                page.wait_for_timeout(1500)

            # Step 3: 填写表单（使用更宽松的等待策略）
            log("INFO", "Step 3: 填写登录表单...")

            # 使用 page.evaluate 等待元素真正可见
            wait_result = page.evaluate("""
                async () => {
                    let attempts = 0;
                    const maxAttempts = 30; // 最多等待 15 秒

                    while (attempts < maxAttempts) {
                        const username = document.querySelector('input#username');
                        const password = document.querySelector('input#password');
                        const submit = document.querySelector('button[type="submit"]');

                        if (username && password && submit &&
                            username.offsetParent !== null &&
                            password.offsetParent !== null &&
                            submit.offsetParent !== null) {
                            return {
                                success: true,
                                waitTime: attempts * 500
                            };
                        }

                        await new Promise(resolve => setTimeout(resolve, 500));
                        attempts++;
                    }

                    return {
                        success: false,
                        hasUsername: !!document.querySelector('input#username'),
                        hasPassword: !!document.querySelector('input#password'),
                        hasSubmit: !!document.querySelector('button[type="submit"]')
                    };
                }
            """)

            if not wait_result.get("success"):
                raise Exception(f"表单元素未出现: {wait_result}")

            log("INFO", f"  表单元素已就绪（等待 {wait_result.get('waitTime')}ms）")

            # 填写用户名（使用 locator 并等待）
            try:
                username_locator = page.locator('input#username')
                username_locator.wait_for(state="visible", timeout=5000)
                username_locator.click(timeout=5000)
                username_locator.fill(USERNAME, timeout=5000)
                log("INFO", "  ✓ 已填写用户名")
            except Exception as e:
                raise Exception(f"填写用户名失败: {e}")

            # 等待一下
            page.wait_for_timeout(500)

            # 填写密码
            try:
                password_locator = page.locator('input#password')
                password_locator.wait_for(state="visible", timeout=5000)
                password_locator.click(timeout=5000)
                password_locator.fill(PASSWORD, timeout=5000)
                log("INFO", "  ✓ 已填写密码")
            except Exception as e:
                raise Exception(f"填写密码失败: {e}")

            # 等待一下
            page.wait_for_timeout(1000)

            # Step 4: 点击提交按钮
            log("INFO", "Step 4: 点击提交按钮...")
            try:
                submit_locator = page.locator('button[type="submit"]')
                submit_locator.wait_for(state="visible", timeout=5000)
                submit_locator.click(timeout=5000)
                log("INFO", "  ✓ 已点击提交按钮")
            except Exception as e:
                raise Exception(f"点击提交按钮失败: {e}")

            # Step 5: 等待登录完成
            log("INFO", "Step 5: 等待登录响应...")
            page.wait_for_timeout(3000)

            # 检查是否有滑块验证
            has_captcha = page.evaluate("""
                () => !!document.querySelector('#nc_1_n1z, .nc-container, [class*="captcha"]')
            """)

            if has_captcha:
                log("WARN", "检测到滑块验证码，等待处理...")
                page.wait_for_timeout(5000)

            current_url = page.url
            log("INFO", f"  当前 URL: {current_url}")

            # Step 6: 使用登录后的浏览器会话调用用户信息接口
            log("INFO", "Step 6: 获取用户信息...")

            api_result = page.evaluate("""
                async () => {
                    try {
                        let userStr = null;
                        for (let attempt = 0; attempt < 10; attempt++) {
                            userStr = localStorage.getItem('user');
                            if (userStr) break;
                            await new Promise(resolve => setTimeout(resolve, 500));
                        }

                        if (!userStr) {
                            return { success: false, error: '登录后未找到用户 ID' };
                        }

                        const localUser = JSON.parse(userStr);
                        if (!localUser.id) {
                            return { success: false, error: '登录用户 ID 无效' };
                        }

                        const response = await fetch('/api/user/self', {
                            method: 'GET',
                            headers: {
                                'Accept': 'application/json, text/plain, */*',
                                'New-API-User': String(localUser.id)
                            },
                            credentials: 'include',
                            cache: 'no-store'
                        });

                        let payload;
                        try {
                            payload = await response.json();
                        } catch (err) {
                            return {
                                success: false,
                                status: response.status,
                                error: '用户信息接口未返回 JSON'
                            };
                        }

                        return {
                            success: response.ok,
                            status: response.status,
                            payload
                        };
                    } catch (err) {
                        return {
                            success: false,
                            error: err.toString()
                        };
                    }
                }
            """)

            if not api_result.get("success"):
                status = api_result.get("status")
                error = api_result.get("error") or "请求失败"
                if status:
                    raise Exception(f"获取用户信息失败（HTTP {status}）: {error}")
                raise Exception(f"获取用户信息失败: {error}")

            payload = api_result.get("payload")
            if not isinstance(payload, dict) or payload.get("success") is not True:
                message = payload.get("message") if isinstance(payload, dict) else "响应格式错误"
                raise Exception(f"获取用户信息失败: {message or '接口返回失败'}")

            user_data = payload.get("data")
            if not isinstance(user_data, dict):
                raise Exception("获取用户信息失败: 响应中缺少 data")

            quota = user_data.get("quota")
            if isinstance(quota, bool) or not isinstance(quota, (int, float)):
                raise Exception("获取用户信息失败: data.quota 不是有效数字")

            result = {
                "user_id": user_data.get("id", 0),
                "username": user_data.get("username") or USERNAME,
                "quota": quota,
                "checked_in": None,
            }
            log("INFO", "  ✓ 已从 /api/user/self 获取用户信息")
            log("INFO", f"  ✓ 用户 ID: {result['user_id']}")
            log("INFO", f"  ✓ 用户名: {result['username']}")
            log("INFO", f"  ✓ quota: {result['quota']}")

        except PlaywrightTimeoutError as e:
            log("ERROR", f"页面操作超时: {e}")
            log("ERROR", f"当前 URL: {page.url}")
            # 截图用于调试
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path)
                log("INFO", f"已保存错误截图: {screenshot_path}")
            except:
                pass

        except Exception as e:
            log("ERROR", f"浏览器自动化登录失败: {e}")
            log("ERROR", traceback.format_exc())

        finally:
            browser.close()

    return result

def format_balance(quota: int) -> str:
    """将 quota 转换为美元显示（假设 500000 = $1）"""
    if quota is None:
        return "N/A"
    balance = quota / 500000
    return f"{balance:.2f}$"

def run_checkin():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log("INFO", "=" * 50)
    log("INFO", "AgentRouter 登录签到脚本启动")
    log("INFO", f"时间: {now_str}")
    log("INFO", f"用户名: {USERNAME}")
    log("INFO", "=" * 50)

    if not USERNAME or not PASSWORD:
        log("ERROR", "USERNAME 或 PASSWORD 未配置，请设置环境变量")
        sys.exit(1)

    # ---------- Step 1: 获取干净的代理 IP ----------
    login_result = None
    used_proxy = None

    # 通道一：自有干净节点（setup-proxy.sh + sing-box 写入 PROXY_SERVER）
    if PROXY_SERVER:
        log("INFO", f"检测到环境变量代理 {PROXY_SERVER}，优先使用自有节点登录...")
        login_result = browser_login_complete()
        if not login_result:
            log("WARN", "自有节点登录失败，回退到代理池探测...")

    # 通道二：代理池探测（未配置自有节点 / 自有节点登录失败时）
    if not login_result:
        try:
            clean_proxies = find_clean_proxies()
        except Exception as e:
            log("ERROR", f"代理池探测异常: {e}")
            clean_proxies = []

        if not clean_proxies:
            log("ERROR", "代理池扫描完成，未找到无 WAF 拦截的可用代理")

        # 用干净代理逐个尝试登录（登录即签到）
        for idx, proxy in enumerate(clean_proxies[:MAX_LOGIN_ATTEMPTS], start=1):
            log("INFO", "=" * 50)
            log(
                "INFO",
                f"[尝试 {idx}/{min(len(clean_proxies), MAX_LOGIN_ATTEMPTS)}] "
                f"使用代理 {mask_proxy(proxy['ip'], proxy['port'])} (anonymity={proxy['anonymity']}, speed={proxy['speed']}ms) 登录...",
            )
            attempt = browser_login_complete(proxy)
            if attempt:
                login_result = attempt
                used_proxy = proxy
                break
            log("WARN", f"代理 {mask_proxy(proxy['ip'], proxy['port'])} 登录失败，换下一个...")

    if not login_result:
        log("ERROR", "浏览器自动化登录失败")
        send_telegram(
            f"❌ <b>AgentRouter 登录失败</b>\n"
            f"👤 账户: {USERNAME}\n"
            f"⏱️ 时间: {now_str}\n"
            f"📝 原因: 浏览器自动化登录失败"
        )
        sys.exit(1)

    user_id = login_result.get("user_id", 0)
    username = login_result.get("username", USERNAME)
    balance = format_balance(login_result.get("quota", 0))

    log("INFO", f"✅ 登录成功！")
    if used_proxy:
        log("INFO", f"使用代理: {mask_proxy(used_proxy['ip'], used_proxy['port'])}")
    log("INFO", f"用户 ID: {user_id}")
    log("INFO", f"用户名: {username}")
    log("INFO", f"当前余额: {balance}")
    log("INFO", f"🎁 通过登录完成签到")

    # ---------- Step 2: 发送 Telegram 通知 ----------
    message = (
        f"🎁 <b>AgentRouter 签到通知</b>\n\n"
        f"👤 登录账户: {USERNAME}\n"
        f"💰 当前余额: {balance}\n"
        f"📋 状态: 通过登录完成签到\n"
        f"⏱️ 时间: {now_str}"
    )

    send_telegram(message)

    log("INFO", "=== 脚本执行完毕 ===")

def main():
    try:
        run_checkin()
    except KeyboardInterrupt:
        log("WARN", "用户中断")
        sys.exit(130)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        log("ERROR", f"脚本执行出错: {error_msg}")
        log("ERROR", traceback.format_exc())
        send_telegram(
            f"❌ <b>AgentRouter 脚本异常</b>\n"
            f"👤 账户: {USERNAME}\n"
            f"⏱️ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📝 错误: {error_msg}"
        )
        sys.exit(1)

if __name__ == "__main__":
    main()
