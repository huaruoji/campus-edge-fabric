# Campus Edge Fabric

把队员手里闲置的电脑拼成一台"临时服务器"：成员在自己电脑上跑一条命令，设备就加入算力池；用户从网页提交批量任务，结果自动汇总下载。

> 比赛要求：1 天内做出**真实多设备协同**的计算服务，能现场验证有效计算、对比单机/多机吞吐、说清产品与成本。
> 本仓库当前是**可运行骨架**（已经跑通：提交 → 多设备领取 → 本地真实执行 → 汇总下载），不是概念演示。

---

## 1. 现在有什么（已验证）

```
coordinator (FastAPI + SQLite) ── 设备注册/心跳/租约调度/结果汇总/仪表盘
worker agent (Python)          ── 主动拉取任务 → 本地执行 → 回传结果与耗时
workload adapters              ── thumbnail(CPU 图像批处理) / infer(ONNX 目标检测) / blender(逐帧渲染)
```

端到端 smoke test 已跑通（本机 1 worker，12 张 1200×800 JPEG）：

```
job consider_… 12 task(s) → 12/12 succeeded → result.zip (manifest.json + 12 个 webp)
per_worker: [{"name":"dev-a","completed":12,"mean_duration_s":0.047,"share":1.0}]
stats: tasks/min 6.0  p50 0.041s  retries 0
```

覆盖到的调度行为（有单测，见 `packages/cef-coordinator/tests`）：

- **Pull 调度**：Worker 有空闲容量才领取，快机器自然多做
- **租约 + fencing token**：重复/迟到结果按 attempt 丢弃，不重复计数（409）
- **超时回收**：租约过期自动重排，超过 `max_attempts` 判失败
- **能力/标签匹配**：任务只能被装有对应 workload 的设备领取；`required_tag` 可把任务钉在指定设备（基准测试用）
- **自适应批量**：`capacity = target_lease_s / EWMA(单任务耗时)`，并按 CPU/GPU 负载收敛，避免把 20 个长任务塞给一台机器

## 2. 5 分钟跑起来

要求：Python ≥ 3.11，[uv](https://docs.astral.sh/uv/)。

**A. 起 Coordinator（找一台稳定的机器）**

```bash
git clone <this repo> && cd campus-edge-fabric
./deploy/coordinator.sh            # 首次会装依赖、生成 token、打印访问地址
```

它会打印：

```
data dir : …/cef-data
token    : xxxxxxxxxxxxx        # 所有客户端都用这个
dashboard: http://0.0.0.0:8080/
api docs : http://0.0.0.0:8080/docs
```

**B. 每台队员电脑加入（Windows 用 PowerShell）**

```bash
# Linux / macOS
./deploy/worker.sh --server http://192.168.1.10:8080 --token <TOKEN> --name my-laptop --slots 2
```
```powershell
# Windows
.\deploy\worker.ps1 -Server http://192.168.1.10:8080 -Token <TOKEN> -Name my-pc -Slots 2
```

**C. 提交任务**

```bash
uv run cef --server http://192.168.1.10:8080 --token <TOKEN> doctor
uv run cef ... job submit --workload thumbnail --params '{"size":512}' ~/photos/*.jpg
uv run cef ... job watch <job_id>
uv run cef ... job result <job_id> -o out.zip
```

也可以直接用网页 `http://<coordinator>:8080/`（上传 → 看进度 → 下载）或 `/docs` 里的 OpenAPI。

### 2.1 让 agent 帮你递交（可选，但很省事）

三种方式操作同一个 API，权限完全一样：

```bash
# 1) 声明式递交（推荐，等价于 sbatch job.sh）
uv run cef job submit --spec examples/job-thumbnail.json

# 2) 任何 coding agent 都能直接跑 CLI（Claude Code / Codex 都能执行 bash）
#    AGENTS.md 里已经写好它们该用哪些命令

# 3) MCP：让 Claude Code / Codex 把 CEF 当成一组原生工具
claude mcp add cef -- uv run --project "$PWD" cef mcp \
  --server https://cef.huaruoji.com --token "$CEF_TOKEN"
```

MCP 暴露的工具：`list_workloads / list_workers / pool_stats / submit_job / job_status /
watch_job / job_manifest / download_result / cancel_job / health`。

Codex 的配置写在 `AGENTS.md` 第 2 节（`~/.codex/config.toml` 的 `[mcp_servers.cef]`）。

## 3. 仓库结构

```
packages/
  cef-protocol/       线上协议：pydantic 模型、状态机、workload 参数、HTTP SDK（唯一契约来源）
  cef-coordinator/    FastAPI + SQLite + 租约调度 + 静态仪表盘（一个进程＝整个控制面）
  cef-worker/         设备 agent + 三个 workload adapter
  cef-client/         cef 命令行（与 Web / HTTP 同一套 API）
web/                  React + Vite 仪表盘（构建产物进 coordinator 包，部署仍是单进程）
benchmarks/           P_i / P_pool / η / S 测量脚本
deploy/               一键启动脚本（sh / ps1）
docs/                 api.md（接口契约）、architecture.md（设计取舍）
```

## 4. 关键设计取舍

| 决定 | 为什么 |
|---|---|
| Coordinator 单进程（API+静态页+调度器） | 部署只有一条命令，评委现场不用调编排 |
| SQLite + 纯 SQL，不上 ORM | 状态机只有十几条 SQL，现场能直接解释；WAL 足够这个并发量 |
| Pull + 租约，而不是 Push | 设备随时退出/休眠都不需要服务端知道"谁忙"，故障靠租约过期回收 |
| attempt 作为 fencing token | 重试必然发生，没有 fencing 就会重复计数 = 指标造假 |
| Workload 白名单 + 固定 argv | Worker 永不执行网络下发的任意命令，只跑本地已安装的 adapter |
| 静态资源打进 Python 包 | `cef-coord serve` 就是全部，不需要同时跑 node |

细节见 [docs/architecture.md](docs/architecture.md) 和 [docs/api.md](docs/api.md)。

## 5. 实验怎么测（比赛硬指标）

```bash
# 每台设备先注册一个唯一 tag，用来做单机基线
./deploy/worker.sh ... --tags bench-pc1
./deploy/worker.sh ... --tags bench-pc2

uv run python benchmarks/bench.py \
  --server http://127.0.0.1:8080 --token <TOKEN> \
  --workload thumbnail --tasks 64 --inputs ~/bench-images \
  --tag-prefix bench
```

脚本会依次测 `P1, P2, …`（用 `required_tag` 把任务钉在单台设备上，其余设备照常在线），
再测 `P_pool`（不限设备），并输出：

- `吞吐 = 正确完成的任务数 / 墙钟时间`（失败任务不计入）
- `η = P_pool / ΣP_i`，`S = P_pool / max(P_i)`
- 每台设备 CPU/GPU 利用率平均值（来自心跳采样）、等待时间、重试次数

结果写到 `benchmarks/results/`（JSON + Markdown 表格，可直接贴进材料）。

## 6. 分工建议（4 人）

| 人 | 负责 | 目录 |
|---|---|---|
| A | 调度正确性、租约/fencing、实验脚本、性能结论 | `packages/cef-coordinator`、`benchmarks/` |
| B | Worker agent、workload adapter（infer / blender）、依赖检测 | `packages/cef-worker` |
| C | 仪表盘、提交体验、结果下载、演示脚本 | `web/`、`deploy/` |
| D | 产品故事、成本估算、README/材料、现场流程串演 | `docs/`、README |

接口改动只走 `packages/cef-protocol` + `docs/api.md`，谁改谁先同步。

## 7. 未完成 / 明确声明

- **模型文件不随仓库分发**：`infer` 需要把 `*.onnx` 放到 `~/.cef/models/`（或 `--model-dir`）
- `blender` adapter 需要在每台设备预装 Blender（版本尽量一致），当前只做了逐帧渲染路径
- 鉴权是单一共享 token（LAN/Tailscale 内使用），没有做多租户与细粒度权限
- 没有容器编排 / 自动迁移：Worker 掉线后任务会被重排到其他设备，但不会保留半成品
- 商业论证目前只有假设，等真实团队试用
