# MyProxy Panel

MyProxy Panel 是面向单台个人服务器的轻量代理节点控制面板。它用 FastAPI 管理状态和配置，用官方 sing-box Stable 承载 Hysteria2、TUIC 与 Shadowsocks 2022，并生成可直接导入 Mihomo / Clash Meta 客户端的订阅。

项目只面向单服务器、单管理员和个人使用场景，不包含注册、计费、套餐、多用户、集群或机场管理功能。

## 核心能力

- 单管理员登录、Argon2id 密码、数据库会话与首次登录强制改密
- Hysteria2、TUIC、Shadowsocks 2022 节点管理、启停和安全凭证重生
- sing-box 配置生成、staging 校验、原子替换、最多 20 份备份和失败自动回滚
- Mihomo 完整订阅与 Proxy Provider 订阅
- CPU、内存、磁盘、运行时间、逐节点 TCP/UDP 监听、UDP 缓冲与脱敏日志
- 面向 QUIC 的 16 MiB 系统缓冲、保守拥塞参数、客户端握手容错与部署健康检查
- 深色、响应式 React 管理界面
- 项目内 CLI，以及安装、更新、备份、恢复和卸载脚本
- 项目本地 Python、Node.js 和 sing-box Runtime，不替换 Ubuntu 系统 Python

## 架构

```text
Browser
  │ HTTPS
  ▼
Nginx ── static ──> React dist
  │
  ├── /api/* ─────> FastAPI 127.0.0.1:8000 ──> SQLite
  │                                      └────> config/apply/rollback
  └── /sub/* ─────> FastAPI                    │
                                                ▼
                                        official sing-box
                                      HY2 / TUIC / SS2022
```

FastAPI 是控制面，sing-box 是数据面。项目不会自行实现任何代理协议。

## 环境要求

生产目标：

- Ubuntu 20.04.6 LTS
- 1 vCPU、1 GB RAM 或更高
- amd64 或 arm64
- root 或 sudo 权限用于首次安装和系统服务集成
- 域名模式需要已解析到服务器 IPv4 的 A 记录

项目锁定的 Runtime 版本见 `.runtime-versions`。安装器只把应用 Runtime 放在 `/opt/myproxy/.runtime`；系统级仅使用 Nginx、systemd、证书和必要下载工具。

当前锁定的代理核心是官方 [sing-box v1.14.1 Stable](https://github.com/SagerNet/sing-box/releases/tag/v1.14.1)。下载器同时核对仓库固定 SHA256 与 GitHub Release 元数据，不跟随未经验证的 `latest` 地址。

## 端口与安全组

默认需要开放：

| 端口 | 协议 | 用途 |
| --- | --- | --- |
| 22 | TCP | SSH |
| 80 | TCP | ACME / HTTP 跳转 |
| 443 | TCP | 面板与订阅 HTTPS |
| 8443 | UDP | Hysteria2 |
| 10443 | UDP | TUIC |
| 8388 | TCP + UDP | Shadowsocks 2022 |

不要向公网开放 8000 端口。FastAPI 只监听 `127.0.0.1:8000`。

除了服务器本机防火墙，还要在云厂商后台的 Security Group、Firewall 或 ACL 中开放相同端口。只修改 UFW 而忽略供应商安全组，会导致节点仍无法连接。

## 安装

### 域名与正式证书

先将域名 A 记录解析到服务器 IPv4，然后执行：

```bash
git clone https://github.com/tokedoge666-max/myproxy-panel.git myproxy-panel
cd myproxy-panel
sudo env \
  SERVER_IP="203.0.113.10" \
  DOMAIN="panel.example.com" \
  ACME_EMAIL="admin@example.com" \
  bash deploy/install.sh
```

### 暂无域名的自签名模式

```bash
git clone https://github.com/tokedoge666-max/myproxy-panel.git myproxy-panel
cd myproxy-panel
sudo env \
  SERVER_IP="203.0.113.10" \
  SELF_SIGNED_MODE=true \
  bash deploy/install.sh
```

自签名模式会在界面中标记为 Development / Self Signed Mode，生成的 Mihomo 节点会启用 `skip-cert-verify`。它适合首次测试，不建议长期替代正式证书。

为保证 root 更新流程不会执行仓库内的凭据助手、SSH 命令或 Git Hook，安装器要求源码来自不含用户名/Token 的公开 HTTPS Git origin；ZIP 源码包、SSH remote 和带凭据的 URL 会被拒绝。私有仓库请先发布到由你控制的只读 HTTPS 镜像，再从该镜像安装。

安装器会：

1. 创建 `/opt/myproxy` 和非 root 的 `myproxy` 用户。
2. 下载并校验项目专用 uv、Python 3.12、Node.js 和官方 sing-box Stable。
3. 顺序安装后端依赖和构建前端，避免 1 GB 服务器并发构建造成内存不足。
4. 安全生成管理员临时密码、订阅 Token 和三类节点凭证。
5. 生成配置并执行真实 `sing-box check`。
6. 安装 systemd 与 Nginx 配置，启动服务并执行健康检查。
7. 输出面板地址、临时密码、订阅地址和防火墙提醒。

请立即保存安装完成时显示的临时密码。完整凭证不会写入普通日志；首次登录后必须修改管理员密码。

## 目录隔离

生产目录如下：

```text
/opt/myproxy/
├── .runtime/          # uv、Node.js、sing-box
├── backend/.venv/     # 仅项目使用的 Python 环境
├── frontend/dist/     # Nginx 提供的静态文件
├── config/            # app.env、证书引用、sing-box.json
├── data/              # SQLite
├── logs/              # API、审计和 sing-box 日志
├── backups/sing-box/  # 配置备份，最多 20 份
├── run/               # staging 和运行期文件
├── deploy/
└── myproxy            # 项目内运维 CLI
```

项目不会：

- 修改 `/usr/bin/python` 或 `/usr/bin/python3`
- 使用全局 pip 或 npm 安装
- 通过 apt 安装 Node.js Runtime
- 把 sing-box 复制到 `/usr/bin` 或 `/usr/local/bin`
- 让 FastAPI 以 root 身份运行

## 使用面板

### Dashboard

查看 CPU、内存、磁盘、运行时间、sing-box 状态、每个节点的实际监听端口和 UDP 缓冲上限。面板只验证服务器本机状态；公网防火墙、云安全组和客户端链路仍需在 Mihomo 客户端实测。

### Nodes

管理默认节点：

- `LA-HY2`：Hysteria2，默认 UDP 8443
- `LA-TUIC`：TUIC v5，默认 UDP 10443
- `LA-SS2022`：Shadowsocks 2022，默认 TCP/UDP 8388

节点修改先保存为期望状态。应用配置时，系统会生成 staging 文件、执行 `sing-box check`、备份旧配置、原子替换并检查重启结果；失败时自动恢复上一份配置。

### Subscription

页面提供两类地址：

- Mihomo 完整订阅：含 `Proxy` 手动选择组和 `MATCH,Proxy` 规则
- Proxy Provider：只含 `proxies`，可接入已有 Mihomo 配置

轮换订阅 Token 后，旧地址立即失效。不要把订阅地址贴到工单、聊天群或公开仓库。

### Settings

可以修改面板显示名称，并查看当前服务器 IPv4、域名、TLS 路径和证书模式。后四项同时影响 Nginx、FastAPI 与 sing-box，因此在 Web 中只读，必须通过下面的受控重配置脚本修改。重启、全部重生凭证和恢复备份均需二次确认。

## Mihomo / Clash Verge 导入

在 Subscription 页面复制 Mihomo Subscription URL，然后在 Clash Verge Rev、Mihomo Party 或其他 Mihomo 兼容客户端中新增远程配置。更新后应看到：

```text
LA-HY2
LA-TUIC
LA-SS2022
```

默认策略组为 `Proxy`，类型为 `select`。项目不会默认启用 `url-test`、`fallback` 或负载均衡。

Proxy Provider 地址可用于已有配置：

```yaml
proxy-providers:
  myproxy:
    type: http
    url: "https://panel.example.com/sub/<token>?format=provider"
    path: ./providers/myproxy.yaml
    interval: 3600
```

## 项目 CLI

```bash
cd /opt/myproxy
./myproxy status
./myproxy restart
./myproxy logs
./myproxy check
./myproxy backup
./myproxy restore
./myproxy info
```

`restore` 只接受项目备份目录中的受控文件名。恢复后仍会执行配置检查和服务状态确认。

## 常用运维命令

```bash
systemctl status myproxy-api
systemctl restart myproxy-api
journalctl -u myproxy-api -f

systemctl status myproxy-singbox
systemctl restart myproxy-singbox
journalctl -u myproxy-singbox -f

nginx -t
systemctl reload nginx
```

sing-box 的应用日志也可从面板或 `/opt/myproxy/logs/sing-box.log` 查看。API 与订阅访问日志会过滤密码、Token、Authorization、Cookie、私钥、完整 UUID 和订阅 URL；Nginx 的 `/sub/` 路径关闭 access log。

## 更新

从 sing-box 1.13.16 版本首次升级到本版本时，旧更新器会拒绝新的 Runtime pin。只需执行一次引导更新：

```bash
cd /opt/myproxy
sudo git -c safe.directory=/opt/myproxy -C /opt/myproxy fetch origin main
sudo bash -o pipefail -c 'git -c safe.directory=/opt/myproxy -C /opt/myproxy show origin/main:deploy/bootstrap-update.sh | bash'
```

这条命令会以 root 执行 `origin/main` 中的引导脚本，因此仅用于你已确认 origin 为 `https://github.com/tokedoge666-max/myproxy-panel.git` 的官方公开仓库。引导脚本会把事务更新绑定到本次获取的不可变提交，再执行备份、构建、检查和失败回滚。通过自建只读镜像安装的旧版本应先人工审计并同步这段桥接逻辑。完成这次升级后，后续继续使用普通更新命令：

```bash
cd /opt/myproxy
sudo bash deploy/update.sh
```

更新器会先备份数据库和正式配置，再更新锁定依赖、构建前端、执行测试与 `sing-box check`。新版本启动或健康检查失败时，会恢复更新前的关键文件。

下载依赖、测试和构建期间控制面会暂时停止，已有 sing-box 数据面继续运行；只有最终原子切换时会有一次短暂停机。更新器会以 root 执行当前 Git 分支中受信任的部署代码，因此只应连接到你控制并已审核的无凭据 HTTPS 仓库。更新前建议核对远端提交或签名 Release；仓库分支本身应视为 root 信任边界。

## 部署身份与证书重配置

域名、服务器 IPv4 或证书模式变化时，不要直接改数据库、`app.env` 或 Nginx 文件。使用事务式重配置入口，让控制面、数据面、订阅地址和 TLS 一起切换：

```bash
cd /opt/myproxy

# 切换到域名 + ACME 正式证书
sudo bash deploy/reconfigure.sh \
  --server-ip 203.0.113.10 \
  --domain panel.example.com \
  --email admin@example.com \
  --acme

# 切换到无域名的自签名模式
sudo bash deploy/reconfigure.sh \
  --server-ip 203.0.113.10 \
  --no-domain \
  --self-signed
```

脚本要求 API 与 sing-box 在开始前均为正常运行状态。它会先建立 root 保护的事务备份，校验证书、Nginx 和新 sing-box 配置，再切换服务；任一步失败都会恢复数据库、配置、证书、环境文件和 Nginx 站点。ACME 模式执行前，请确认新域名的 A 记录已指向目标 IPv4，且公网 80/443 可达。

## 备份与恢复

每次安全应用前，正式 sing-box 配置会备份到：

```text
/opt/myproxy/backups/sing-box/config-YYYYMMDD-HHMMSS.json
```

默认只保留最新 20 份。手工操作前可执行：

```bash
cd /opt/myproxy
./myproxy backup
./myproxy restore
```

数据库备份由更新脚本保存到项目备份目录。建议再使用服务器供应商快照或加密的异机备份保护整个 `/opt/myproxy/data`。

## 卸载

```bash
cd /opt/myproxy
sudo bash deploy/uninstall.sh
```

卸载器会先显示将删除的固定目标并要求确认，然后停止服务、移除 systemd/Nginx 集成、移除受限授权并删除 `/opt/myproxy`。应用 Runtime 全部位于项目目录，不会残留全局 Python/npm 包或全局 sing-box 二进制。

## 本地开发

后端：

```bash
cd backend
UV_PYTHON_INSTALL_DIR=../.runtime/python \
UV_CACHE_DIR=../.runtime/cache/uv \
UV_PYTHON_PREFERENCE=only-managed \
uv sync --all-groups

MYPROXY_ENV=development \
MYPROXY_HOME=.. \
MYPROXY_ADMIN_PASSWORD='Dev-Only-Password-123!' \
UV_PYTHON_INSTALL_DIR=../.runtime/python \
UV_CACHE_DIR=../.runtime/cache/uv \
uv run uvicorn app.main:app --reload
```

`MYPROXY_ADMIN_PASSWORD` 只在本地数据库第一次创建管理员时使用；已有管理员时会被忽略。不要在生产环境复用示例密码。

前端：

```bash
cd frontend
npm_config_cache=../.runtime/cache/npm npm install
npm run dev
```

完整检查：

```bash
make check
```

开发环境的数据、Runtime、缓存和依赖仍保持在项目目录内。

## 故障排查

### 面板无法访问

1. 检查 `systemctl status myproxy-api` 与 `systemctl status nginx`。
2. 执行 `nginx -t`。
3. 检查 80/443 的 UFW 与云厂商安全组。
4. 确认域名 A 记录指向当前服务器 IPv4。

### 节点无法连接

1. 在 Dashboard 确认 sing-box 正常，节点显示对应 TCP/UDP 端口“已监听”。
2. 执行 `./myproxy check`。
3. 检查对应 UDP/TCP 端口是否同时在 UFW 和供应商安全组开放。
4. 检查客户端订阅是否已更新；Token 轮换后必须使用新地址。
5. TLS 模式下确认域名、SNI 和证书仍有效。

若 Clash Verge Rev 日志出现 `CRYPTO_ERROR 0x178` 或
`server did not select an ALPN protocol`，请先更新服务端，再刷新订阅；HY2 与
TUIC 两端都必须协商 HTTP/3 的 `h3` ALPN。

若面板提示 UDP 缓冲低于 16 MiB，重新执行 `sudo bash deploy/update.sh` 以安装持久化的 QUIC 缓冲配置；该上限不会在启动时预占 16 MiB 内存。

### 配置应用失败

面板会显示 `sing-box check` 的脱敏错误。正式配置不会在校验前被覆盖；若重启失败会自动回滚。可通过 `./myproxy logs` 和审计日志定位原因，必要时从 Settings 或 CLI 恢复最近备份。

### 内存不足

Node.js 只用于构建，不应作为生产进程运行。确认没有 Vite dev server，并检查：

```bash
systemctl status myproxy-api myproxy-singbox nginx
ps aux --sort=-%mem | head
```

## 安全说明

- 管理接口仅经 Nginx HTTPS 暴露，FastAPI 固定监听 localhost。
- 密码使用 Argon2id；会话 Token 仅以摘要形式存入 SQLite。
- 会话 Cookie 使用 HttpOnly、Secure、SameSite=Strict，修改操作校验 CSRF Token。
- 订阅 Token 使用至少 32 byte 的密码学安全随机数并可立即轮换。
- SS2022 的 `2022-blake3-aes-128-gcm` 密钥是 16 byte 随机值的 Base64 编码。
- Backend 和 sing-box 以非 root 的 `myproxy` 用户运行；服务控制只授予固定命令的最小权限。
- 配置先校验、再备份、再原子替换；失败自动回滚。
- SQLite、环境配置、证书私钥和备份不提交 Git。

生产部署后建议定期更新项目锁定版本、操作系统安全补丁和 TLS 证书，并限制 SSH 来源地址。

### 已知安全边界

- v1 的 API 与 sing-box 按轻量单机设计共用 `myproxy` 系统身份。若其中一个进程被攻陷，同一身份下的数据（包括 SQLite 与订阅 Token）也应视为暴露；更高隔离级别应拆分控制面、数据面和构建账号。
- `deploy/update.sh` 信任当前无凭据 HTTPS origin 的目标分支，尚未强制校验签名提交或不可变 Release。只从你控制并已审核的发布源执行更新。
- 自签名和正式证书共用一份 Nginx 模板，因此默认不发送 HSTS。确认域名与正式证书长期稳定后，可在 HTTPS server 块中按自身策略启用。

## 测试范围

项目测试覆盖：

- 管理员认证、密码哈希、数据库会话与首次改密
- 凭证随机性、UUIDv4、SS2022 Base64 长度、订阅 Token
- 节点 CRUD、端口与协议配置校验
- 三协议启停、TLS、配置生成、备份与回滚
- Mihomo/Provider YAML、无效 Token、禁用节点排除
- API 权限、公开健康检查和敏感信息脱敏
- 前端 TypeScript、lint 与生产构建

最终的 systemd、Nginx、真实服务启动与资源占用验收应在干净的 Ubuntu 20.04.6 目标机执行。

## License

Private use. Add an explicit license before redistributing the project.
