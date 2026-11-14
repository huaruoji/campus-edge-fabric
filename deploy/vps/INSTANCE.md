# 线上实例登记（不含任何密钥）

生产 coordinator 跑在一台公网 VPS 上，队友不需要 Tailscale、不需要开自己电脑的端口。

| 项 | 值 |
|---|---|
| 访问入口 | **https://cef.huaruoji.com**（Web 仪表盘 / REST API / OpenAPI `/docs`） |
| 主机 | DMIT，Los Angeles |
| 公网 IP | 179.253.239.163 |
| 配置 | 1 vCPU / 2 GB RAM / 20 GB SSD（系统盘已用 ~4 GB） |
| 月流量 | 400 GB（与代理流量共享，注意别用大图跑基准） |
| 系统 | Debian，Python 由 uv 管理（3.12，装到 `/opt/pythons`） |
| 代码目录 | `/opt/campus-edge-fabric`（git clone，deploy key 只读） |
| 数据目录 | `/var/lib/cef`（SQLite + blob） |
| Token | `/etc/cef/token`（0600；**只写路径，值不入库**） |
| 服务 | `cef-coord.service`（监听 `127.0.0.1:8090`） |
| 自动部署 | `cef-autodeploy.timer`：每 60 s 拉 `origin/main`，依赖重装 + 重启，健康检查失败会非零退出 |
| 反向代理 | Caddy（`127.0.0.1:8443`），站点块见 `deploy/vps/caddy.snippet` |
| 入口链路 | 浏览器 → `:443` xray(Reality fallback) → Caddy `:8443` → `127.0.0.1:8090` |
| DNS | `A cef.huaruoji.com → 179.253.239.163`（DNS only，走 Let's Encrypt 通配证书） |
| 部署密钥 | GitHub deploy key `cef-vps-dmit`（read-only），私钥在 VPS `/root/.ssh/cef_deploy` |

## 注意

- **443 属于 xray**，不要往上加站点；所有公网站点都走 `:8443` + Reality fallback。
- 改 Caddyfile 必须：先备份 → `caddy validate` → `systemctl reload caddy`。
- 站点块里 **`bind 127.0.0.1` 不能省**：少了它 Caddy 会绑 `*:8443`，把同机的 3x-ui 面板代理一起暴露到公网（已踩过一次）。
- 磁盘只有 16 GB 可用：输入图先压到 ≤500 KB，别让 blob 无限增长（GC 待实现）。
- 备份：`/etc/caddy/Caddyfile.bak.*`。

## 队友怎么连

```bash
git clone https://github.com/huaruoji/campus-edge-fabric
cd campus-edge-fabric
./deploy/worker.sh --server https://cef.huaruoji.com --token <向队长要> --name my-pc --slots 2
```

Windows：

```powershell
.\deploy\worker.ps1 -Server https://cef.huaruoji.com -Token <向队长要> -Name my-pc -Slots 2
```
