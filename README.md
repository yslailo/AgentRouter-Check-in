# AgentRouter 自动签到脚本

每天自动登录 [AgentRouter](https://agentrouter.org/register?aff=Rm0L) 完成签到，获取每日奖励。

## ⚙️ 功能特性

- ✅ 使用账号密码自动登录（登录即签到）
- ✅ 双通道代理：优先自有节点（sing-box），失败自动回退免费代理池
- ✅ 代理池 WAF 探测：随机抽样并发检测 IP 是否触发阿里云 WAF
- ✅ Telegram 通知（可选）
- ✅ GitHub Actions 自动运行（每天北京时间 10:00）

## 🚀 快速开始

### 1. Fork 本仓库

点击右上角 **Fork** 按钮，将仓库复制到你的账号下。

### 2. 配置 Secrets

前往仓库的 **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，添加以下配置：

#### 必须配置：

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

1. **登录即签到**：AgentRouter 的签到机制是"登录即完成签到"
2. **浏览器自动化**：使用 Playwright 模拟真实浏览器登录
3. **双通道代理**：优先自有节点；未配置或失败时从免费代理池并发探测无 WAF 干净 IP 兜底
4. **每日自动运行**：GitHub Actions 定时任务

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
4. 查看是否有错误截图（`page_error.png`）

## 📜 更新日志

- **2026-08-29**: 新增免费代理池兜底通道（并发 WAF 探测、多轮抽样、IP 日志打码）
- **2026-08-07**: 添加代理支持，绕过 WAF 验证
- **2026-08-07**: 改为账号密码登录模式
- **2026-08-06**: 初始版本

## 🙏 致谢

- [CharlesPikachu/freeproxy](https://github.com/CharlesPikachu/freeproxy) —— 感谢作者提供的免费代理池服务，本脚本的兜底代理通道基于该项目公开的代理数据

## 📄 许可证

MIT License
