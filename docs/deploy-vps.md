# 部署：VPS 上的 Coordinator（公网入口）

目标：任何一台电脑（校园网、家宽、手机热点）都能加入算力池，不需要 Tailscale、不需要开放个人电脑端口。
架构上 coordinator 只做控制面，**但当前版本的文件数据也走它**，所以下面有明确的量和配额约束。

## 0. 实测基线（2026-11-14，本机 → DMIT LA）

| 指标 | 实测 |
|---|---|
| RTT | 147 ms（0% 丢包） |
| 上传 | 8 MB / 4.8 s ≈ **1.7 MB/s** |
| 下载 | 8 MB / 5.1 s ≈ **1.6 MB/s** |
| 磁盘 | 20 GB，已用 3.6 GB（剩 16 GB） |
| 内存 | 1.9 GB（可用 1.2 GB） |
| 流量 | 400 GB/月（与他人代理共享，日常占用约 0.6 GB/天） |

推论：
- **控制流量可以忽略**（心跳 + 轮询，几 KB/s）。
- **文件传输是瓶颈**：一张 2 MB 图从 LA 拉下来 1.2 s，而 CPU 上缩略图只要 40 ms。
- 因此：输入必须预先压缩到 ~300 KB 级；Worker 必须本地缓存（同一份数据每台设备只下一次）；
  数据集要复用（同一 dataset 跑多轮基准，而不是每轮重传）。

## 1. 一次安装

```bash
ssh dmit
git clone https://github.com/huaruoji/campus-edge-fabric /opt/campus-edge-fabric  # 私有仓库需 deploy key
```

```bash
# 1) uv + 固定 Python 版本（系统自带 3.14，太新，不用它）
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv python install 3.12

# 2) 依赖
cd /opt/campus-edge-fabric
~/.local/bin/uv sync --all-packages --frozen --python 3.12

# 3) token（只在这里生成一次，所有人用它）
install -d -m 700 /etc/cef
head -c 24 /dev/urandom | base64 | tr -d '/+=' > /etc/cef/token
chmod 600 /etc/cef/token
cat /etc/cef/token      # 发给队友

# 4) 数据目录（磁盘只有 16 GB，必须设上限）
install -d -o root -g root -m 750 /var/lib/cef

# 5) systemd
cp deploy/vps/cef-coord.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now cef-coord
systemctl status --no-pager cef-coord | head -5
curl -s http://127.0.0.1:8090/api/v1/health
```

## 2. 公网 HTTPS

443 端口被 xray 占用（Reality，SNI 不在 serverNames 时回落到 caddy:8443），
所以只改 Caddy，不开新端口：

```bash
cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak.$(date +%F-%H%M)
cat deploy/vps/caddy.snippet >> /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

DNS：`A cef.huaruoji.com → 179.253.239.163`（**DNS only**，灰云）。
CF token 在 `/root/.acme.sh/huaruoji.com_ecc/huaruoji.com.conf`（Zone:DNS:Edit）。

验证：

```bash
curl -s https://cef.huaruoji.com/api/v1/health
```

## 3. 队友加入

```bash
./deploy/worker.sh --server https://cef.huaruoji.com --token <TOKEN> --name my-pc --slots 2
```
Windows：

```powershell
.\deploy\worker.ps1 -Server https://cef.huaruoji.com -Token <TOKEN> -Name my-pc -Slots 2
```

## 4. 容量与配额（必须遵守，否则会把盘或流量跑爆）

- **输入图片先压到 ≤ 500 KB**（长边 1024、JPEG q85 即可满足 infer/thumbnail）：
  ```bash
  python -c "from PIL import Image; import sys,pathlib
for p in pathlib.Path('in').glob('*'):
    im=Image.open(p); im.thumbnail((1024,1024)); im.convert('RGB').save(f'out/{p.stem}.jpg', quality=85)"
  ```
- **一次性数据集只上传一遍**，之后用 `--dataset` 复用（缓存命中后设备不再下载）。
- **保留策略**：默认 7 天 / 单 job 结果保留，超限由 GC 清理（`cef-coord gc`）。
- 单次演示估算：500 张 × 300 KB ≈ 150 MB 上传 + 每台设备 150 MB 下载 ≈ **0.6 GB**；
  400 GB/月 可支撑几十轮完整演示，但**不要用 12 MP 原图直接跑基准**。

## 5. 回滚

```bash
systemctl disable --now cef-coord
cp /etc/caddy/Caddyfile.bak.<stamp> /etc/caddy/Caddyfile && systemctl reload caddy
# 数据都在 /var/lib/cef，删了就没；不删则重启后任务记录还在
```
