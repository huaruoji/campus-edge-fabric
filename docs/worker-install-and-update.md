# 在 NAS / 无 Python 的机器上装 worker，以及怎么更新

## 1. 三条安装路径

| 目标机器 | 推荐方式 | 为什么 |
|---|---|---|
| 有 shell + Python >= 3.11 的 Linux/WSL | 原生 uv | 更新命令最直接（cef-worker update） |
| NAS / 无 Python / 不想污染系统 | Docker | 只依赖 docker，不写宿主系统目录 |
| Windows | deploy/worker.ps1 | 一条 PowerShell |

### 1.1 原生安装

~~~
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <repo> ~/campus-edge-fabric && cd ~/campus-edge-fabric
uv sync --package cef-worker --package cef-client --extra thumbnail
./.venv/bin/cef-worker run --server https://cef.huaruoji.com --token <TOKEN> --name my-pc --slots 2
~~~

### 1.2 Docker（NAS 推荐）

~~~
# 把仓库同步到 NAS（不含 .venv / node_modules / 数据目录）
rsync -az --exclude .venv --exclude node_modules --exclude cef-data \
  ~/projects/campus-edge-fabric/ NAS:/volume1/docker/cef-worker/

# 在 NAS 上
cd /volume1/docker/cef-worker
export CEF_TOKEN=<TOKEN>
docker compose -f deploy/worker.compose.yaml up -d --build
docker compose -f deploy/worker.compose.yaml logs -f
~~~

实测环境：UGREEN NAS，aarch64，Docker 29.6，Python 3.11，/volume1 剩 7TB。
aarch64 上 pillow / numpy / onnxruntime 都有 wheel，构建约 2-3 分钟。

### 1.3 NAS 到底能贡献什么

- 24 小时在线，适合当常驻 CPU 节点（缩略图这类 IO 密集任务很合适）。
- **不会扩充 blob 存储**：文件存在 coordinator 的 blob store，worker 只下载到临时目录、跑完就删。
  想用 NAS 的 7TB 当数据盘，需要另做「存储节点」功能（尚未实现）。
- 没有 GPU，能力是 engine:cpu；infer 能跑但慢。

## 2. 更新

### 2.1 原生安装

~~~
cd ~/campus-edge-fabric
./.venv/bin/cef-worker update --check   # 只对比本地/远端 HEAD
./.venv/bin/cef-worker update          # git pull + uv sync --inexact
sudo systemctl restart cef-worker      # 或 Ctrl-C 后重新运行启动命令
~~~

--inexact 是刻意加的：普通 uv sync 会卸载手工装的包（比如 provision recipe 装的 onnxruntime-gpu），
把 GPU 节点悄悄降级成 CPU 节点。

### 2.2 Docker

~~~
cd /volume1/docker/cef-worker
rsync -az --exclude .venv --exclude node_modules ~/projects/campus-edge-fabric/ ./
docker compose -f deploy/worker.compose.yaml up -d --build
~~~

### 2.3 版本对齐

- worker 注册时上报 agent_version，设备面板可见。
- GET /api/v1/compat 返回协调器版本、最低 worker 版本和更新命令。
- 协调器不会给设备推送代码：它只声明要求，执行在设备本地（与 provision 同一条安全边界）。

## 3. 开机自启（原生安装）

~~~ini
[Unit]
Description=Campus Edge Fabric worker
After=network-online.target

[Service]
WorkingDirectory=/home/<user>/campus-edge-fabric
Environment=CEF_TOKEN=<TOKEN>
ExecStart=/home/<user>/campus-edge-fabric/.venv/bin/cef-worker run --server https://cef.huaruoji.com --name <name> --slots 2 --data /var/lib/cef-worker
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
~~~
