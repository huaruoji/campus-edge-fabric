# Campus Edge Fabric

把队员手里闲置的电脑拼成一台"临时服务器"：成员在自己电脑上跑一条命令，设备就加入算力池；用户从网页提交批量任务，结果自动汇总下载。

**商业化路演**：[打开动画放映版](https://cef.huaruoji.com/pitch/)。空格或方向键推进，`F` 全屏，`N` 打开放映备注。

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

## 2. 安装矩阵：一次装、按角色启动

同一套代码，四个入口；`deploy/install.sh` 只装这个角色需要的东西（不会把 onnxruntime 塞给只做缩略图的机器）：

| 角色 | 命令 | 装了什么 | 启动 |
|---|---|---|---|
| 控制面 | `./deploy/install.sh --role coordinator` | FastAPI + 调度器 + 仪表盘 + CLI | `./.venv/bin/cef-coord serve --host 0.0.0.0 --port 8080` |
| 设备 | `./deploy/install.sh --role worker --extra infer` | agent + CLI（+ onnxruntime） | `./.venv/bin/cef-worker run --server <url> --token <t>` |
| 提交端 | `./deploy/install.sh --role client` | `cef` CLI + MCP server | `./.venv/bin/cef doctor` |
| 开发机 | `./deploy/install.sh --role all` | 全部 + 所有 workload 依赖 | 随意 |

**Worker 机器同时带 CLI**——队友的笔记本既是算力，也是提交端，不需要装两遍。

**NVIDIA 独显推理**：使用[GPU Worker 安装与启停指南](docs/gpu-worker.md)的独立环境和脚本。已完成 [RTX 5060 线上 24 个推理任务验收](docs/gpu-validation-2026-11-14.md)，包含 CUDA 算子证据与 CPU 参考结果对照。
（远端的 VPS 上 coordinator 用的是 `.venv/bin/cef-coord` 直接启动，见 `deploy/vps/INSTANCE.md`。）

**关于 agent 接入**：MCP 是这个领域的现行标准（Claude Code / Codex / Cursor 都支持），但它是**可选的第二入口**——
所有能力在 CLI 上都能做（coding agent 本质就是会跑 bash）。MCP 只是让 agent 少写几层 shell 拼接，装不装都能全覆盖。

## 3. 5 分钟跑起来

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

网页使用已登记数据集：聊天规划 → 选择设备 → 确认任务 → 查看检测图与下载。文件由本地 Agent / CEF CLI 登记；也可使用 `/docs` 里的 OpenAPI。

新版[共享会话与一键对照评测说明](docs/workspace-and-evaluation.md)包含 Worker 升级要求、计量口径和五分钟演示安排。

### 3.1 让 agent 帮你递交（可选，但很省事）

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

## 4. 仓库结构

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

## 5. 关键设计取舍

| 决定 | 为什么 |
|---|---|
| Coordinator 单进程（API+静态页+调度器） | 部署只有一条命令，评委现场不用调编排 |
| SQLite + 纯 SQL，不上 ORM | 状态机只有十几条 SQL，现场能直接解释；WAL 足够这个并发量 |
| Pull + 租约，而不是 Push | 设备随时退出/休眠都不需要服务端知道"谁忙"，故障靠租约过期回收 |
| attempt 作为 fencing token | 重试必然发生，没有 fencing 就会重复计数 = 指标造假 |
| Workload 白名单 + 固定 argv | Worker 永不执行网络下发的任意命令，只跑本地已安装的 adapter |
| 静态资源打进 Python 包 | `cef-coord serve` 就是全部，不需要同时跑 node |

细节见 [docs/architecture.md](docs/architecture.md) 和 [docs/api.md](docs/api.md)。

## 6. 实验怎么测（比赛硬指标）

**D 的已完成实验：[测试入口](docs/testing/README.md) · [Procedure](docs/testing/thumbnail-procedure.md) · [2026-11-14 Report](docs/testing/thumbnail-report-2026-11-14.md)。** 本次固定 thumbnail 对照完成 9 轮正式测量，900 个输出全部校验通过；双 Worker 中位吞吐 3.3055 images/s，相对最快单 Worker 提高 26.17%。该数据为包含上传与下载的服务吞吐，完整设备信息与限制见报告。复跑本次设计请用 `benchmarks/thumbnail/`，下方保留通用基准脚本的用法。

**双 GPU infer：[Procedure](docs/testing/infer-procedure.md) · [报告与图表](docs/testing/infer-2026-11-14/report.md)。** 已完成三规模、三条件、三次重复的 27 轮诊断对照。110 张批次的单机 jochi / 单机 liusic / 双机耗时中位数为 70.08 / 79.05 / 37.50 秒。1350 个正式输出中 1260 个通过固定 CPU 数值参考，未通过项保留在报告中；这是诊断性结果，完整数值一致性验收尚未通过。CPU 与 GPU 测试统一收录在上述测试入口。

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

## 7. 分工建议（4 人）

| 人 | 负责 | 目录 |
|---|---|---|
| A | Coordinator、调度正确性、租约/fencing、接口与集成 | `packages/cef-coordinator`、`packages/cef-protocol` |
| B | Worker agent、workload adapter（infer / blender）、依赖检测 | `packages/cef-worker` |
| C | 仪表盘、提交体验、结果下载、前端联调 | `web/` |
| D | 独立测试、性能对照、技术验收、README 与技术演示 | `benchmarks/`、测试、`docs/`、README |

以上为四位技术成员；商业化另有一位成员负责，不作为当前技术交付前置条件。

**当前交接状态见 [技术分工与交接表](docs/team-handoff.md)。** A 的接口固定与整体联调、B 的 Worker 心跳修复及 D 的自动化回归已记录；D 新增了两个在线 Worker 的 thumbnail 正确性与单机/双 Worker 性能报告。B 仍需补齐物理设备与硬件确认，C 的页面专项验收、真实故障恢复等工作继续；本机双进程测试不等于真实双机验收。

接口改动只走 `packages/cef-protocol` + `docs/api.md`，谁改谁先同步。

Coordinator/Worker 对接请先读 [固定契约 v1](docs/worker-contract-v1.md)。
共享模型快照为 [worker-schema-v1.json](docs/worker-schema-v1.json)，契约测试随 `pytest` 执行。
此前的五份任务 Markdown 保存在 [历史排期目录](docs/team-plan/)；其中 OCR 和旧路径草案不覆盖当前仓库协议。

## 8. 未完成 / 明确声明

- 前端构建：VPS 会在每次 push 后自动 `npm run build`，所以**只推 `web/src/` 也能生效**；仓库里提交的 `dist` 只作为 fresh clone 的兜底
- **模型文件不随仓库分发**：`infer` 需要把 `*.onnx` 放到 `~/.cef/models/`（或 `--model-dir`）
- `blender` adapter 需要在每台设备预装 Blender（版本尽量一致），当前只做了逐帧渲染路径
- 鉴权是单一共享 token（LAN/Tailscale 内使用），没有做多租户与细粒度权限
- 没有容器编排 / 自动迁移：Worker 掉线后任务会被重排到其他设备，但不会保留半成品
- 商业论证目前只有假设，等真实团队试用
