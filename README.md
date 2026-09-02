# AgentRouter 自动签到脚本

每天自动登录 [AgentRouter](https://agentrouter.org/register?aff=Rm0L) 完成签到，获取每日奖励。

## ⚙️ 功能特性

- ✅ 使用账号密码自动登录（登录即签到）
- ✅ 多账号 / 多站点支持：`ACCOUNTS` 多行配置（可带站点前缀），逐账号顺序签到，汇总 Telegram 通知
- ✅ 双通道代理：优先自有节点（sing-box），失败自动回退免费代理池
- ✅ 代理池 WAF 探测：随机抽样并发检测 IP 是否触发阿里云 WAF（按站点独立探测）
- ✅ Telegram 通知（可选，站点域名打码显示）
- ✅ GitHub Actions 自动运行（每天北京时间 10:00）

## 🚀 快速开始

### 1. Fork 本仓库

点击右上角 **Fork** 按钮，将仓库复制到你的账号下。

### 2. 配置 Secrets

前往仓库的 **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，添加以下配置：

#### 必须配置（二选一）：

**方式一：多账号 / 多站点（推荐）**

| Name | Value | 说明 |
|------|-------|------|
| `ACCOUNTS` | 多行文本 | 每行一个账号，格式 `[站点URL\|]邮箱:密码`，可配置任意多个账号、任意多个站点 |

> **示例**（Secret 值中直接换行）：
> ```
> https://agentrouter.org|user1@qq.com:Pass1234
> user2@gmail.com:Abcd5678
> https://example.com|user3@qq.com:Ab:cd
> ```
> - 站点前缀可省略，省略时默认 `https://agentrouter.org`
> - 站点与账号用 `\|` 分隔，密码中可包含冒号
> - 仅兼容 new-api 系站点（登录页与 AgentRouter 同款、有邮箱登录入口）
> - Telegram 通知中站点域名会打码（如 `agen***`），不会完整暴露

**方式二：单账号（兼容旧版）**

| Name | Value | 说明 |
|------|-------|------|
| `USERNAME` | 你的邮箱 | AgentRouter 登录邮箱 |
| `PASSWORD` | 你的密码 | AgentRouter 登录密码 |

#### 代理配置（可选，二选一或都不配）：

| Name | Value | 说明 |
|------|-------|------|
| `NODE_LINK` | 单节点链接或订阅 URL | 优先使用的自有干净节点（vmess/vless/trojan/hysteria2/socks5） |
| `PROXY_CONFIG_URL` | 订阅地址 URL | `NODE_LINK` 为空时的备用订阅地址 |

> **代理工作逻辑**：
> 1. **通道一（优先）**：配置了 `NODE_LINK` / `PROXY_CONFIG_URL` 时，脚本通过 [sing-box](https://github.com/SagerNet/sing-box) 启动自有节点，直接登录
> 2. **通道二（兜底）**：自有节点未配置或登录失败时，自动从 [freeproxy](https://github.com/CharlesPikachu/freeproxy) 代理池随机抽取 100 个 IP，50 线程并发访问登录页检测是否触发阿里云 WAF；凑够 3 个干净 IP 或扫满 5 轮（500 个）为止
> 3. 日志中会对使用的代理 IP 打码（如 `124.248.***.***:1080`）
---
#### Telegram 通知（可选）：

| Name | Value | 说明 |
|------|-------|------|
| `TG_BOT_TOKEN` | Bot Token | 从 [@BotFather](https://t.me/BotFather) 获取 |
| `TG_CHAT_ID` | Chat ID | 你的 Telegram Chat ID |

### 3. 启用 GitHub Actions

1. 前往仓库的 **Actions** 标签页
2. 点击 **I understand my workflows, go ahead and enable them**
3. 脚本将在每天北京时间 10:00 自动运行

### 4. 手动测试运行

前往 **Actions** → 选择 **Ayrouter Daily Check-in** → 点击 **Run workflow**

## 📋 工作原理

1. **登录即签到**：AgentRouter 及同类 new-api 站点的签到机制是"登录即完成签到"
2. **浏览器自动化**：使用 Playwright 模拟真实浏览器登录
3. **多账号 / 多站点循环**：多个账号逐个顺序签到，单个账号失败不影响其他账号；任一失败时 Actions 退出码为 1
4. **双通道代理**：优先自有节点；未配置或失败时从免费代理池并发探测无 WAF 干净 IP 兜底（探测结果按站点共享轮换使用）
5. **每日自动运行**：GitHub Actions 定时任务

## ⚠️ 关于 WAF 验证

AgentRouter 使用了阿里云 WAF，在以下情况会触发滑块验证：
- GitHub Actions 等云服务 IP
- 数据中心 IP
- 频繁请求的 IP

**解决方案**：
1. ✅ **推荐**：配置自有干净节点（`NODE_LINK`），稳定可靠
2. ✅ **兜底**：免费代理池多轮抽样探测（成功率取决于池子质量）

## 🛠️ 常见问题

### Q: 如何获取 Telegram Chat ID？
A: 
1. 向 [@userinfobot](https://t.me/userinfobot) 发送任意消息
2. Bot 会返回你的 Chat ID

### Q: 脚本运行失败怎么办？
A: 
1. 查看 Actions 运行日志
2. 检查是否配置了代理
3. 确认账号密码是否正确
### Q: 其他平台授权登陆如何获取账号密码？
1.授权登入后 进入[个人主页](https://agentrouter.org/console/personal)

2.按照图片指示绑定邮箱

3.退出登陆，再通过邮箱登陆 第一次登陆的时候需要选择忘记密码 然后通过邮箱来重置密码 

4.拿到重置后的密码和邮箱 按照教程填写即可
<img width="2276" height="1415" alt="cac64968dbc60bfa3a90f288da42b6e9" src="https://github.com/user-attachments/assets/eb0d7d10-ecfa-468e-9905-b18eb34594d3" />


## 📜 更新日志

- **2026-08-30**: 支持多站点（`ACCOUNTS` 行可带站点前缀，仅限 new-api 系站点），代理池按站点独立探测
- **2026-08-30**: 支持多账号（`ACCOUNTS` 多行配置），逐账号签到 + 汇总通知
- **2026-08-29**: 新增免费代理池兜底通道（并发 WAF 探测、多轮抽样、IP 日志打码）
- **2026-08-07**: 添加代理支持，绕过 WAF 验证
- **2026-08-07**: 改为账号密码登录模式
- **2026-08-06**: 初始版本

## 🙏 致谢

- [CharlesPikachu/freeproxy](https://github.com/CharlesPikachu/freeproxy) —— 感谢作者提供的免费代理池服务，本脚本的兜底代理通道基于该项目公开的代理数据

## 📄 许可证

MIT License
