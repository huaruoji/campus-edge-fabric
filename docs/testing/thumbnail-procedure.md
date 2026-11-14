# Procedure：thumbnail 正确性与性能对照

维护角色：D。适用范围：两台 Worker 的 CPU 图片缩放服务。对应已完成实验见 [2026-11-14 Report](thumbnail-report-2026-11-14.md)。

## 1. 测什么、由谁运行

D 的电脑负责提交、计时、下载和输出校验，不需要独立显卡，也不必加入算力池。计算由 B 已启动的 Worker 执行。测试走现有 CLI/API；网页承担设备观察与演示，其产品操作验收另做。

先以 16 张图片确认输出正确且两个 Worker 均有实际贡献，再使用 100 张不同图片测三个条件：

| 条件 | 领取限制 | 目的 |
| --- | --- | --- |
| single-1 | `required_tag=TAG1` | Worker 1 单独提供服务的基线 |
| single-2 | `required_tag=TAG2` | Worker 2 单独提供服务的基线 |
| pool | `required_tag=null` | 两个 Worker 共同执行 |

每个条件做三次正式测量，每次前用另外 16 张图片预热。执行顺序依次为 `single-1 → single-2 → pool`、`single-2 → pool → single-1`、`pool → single-1 → single-2`。共 18 个作业，900 个正式输出、144 个预热输出；100 张正式输入在各轮复用，不能称为 900 张不同原图。

## 2. 准备数据和软件

从仓库根目录执行，要求 Python ≥ 3.11 与 uv：

```powershell
uv sync --all-packages --extra thumbnail
```

本次客户端为 Python 3.12.14、Pillow 13.0.0，两个 Worker 也声明 Pillow 13.0.0。重新实验记录实际版本；逐像素对照应保持兼容的 Pillow 行为，不要遇到差异就跳过校验。

按 [数据资料说明](../../benchmarks/datasets/README.md) 下载 BSDS300 图片包，先与 `sources.json` 的 SHA256 比对，再解压。数据根目录应具有以下结构：

```text
<dataset-root>/thumbnail/extracted/BSDS300/images/train/*.jpg
<dataset-root>/thumbnail/extracted/BSDS300/images/test/*.jpg
```

脚本直接读取 `benchmarks/datasets/splits.json` 的 `source_files`，无需额外复制到 `ready/`。冒烟与预热各用 16 张 train 图片，正式测量使用全部 100 张 test 图片。三组文件不重叠。每个输入核对逐图片 SHA256，且不得超过 500,000 字节；本次原图符合预算，不做重新压缩。

默认数据根目录为 `data/test-datasets/`。如果使用与仓库并列的已有 `test-data/`，下面命令可以直接使用；其他位置替换 `--dataset-root`。先运行离线检查，**不加 `--run` 不提交任务**：

```powershell
uv run python benchmarks/thumbnail/run_thumbnail_smoke.py --dataset-root ../test-data
uv run python benchmarks/thumbnail/run_thumbnail_benchmark.py --dataset-root ../test-data
uv run python benchmarks/thumbnail/check_benchmark_helpers.py
```

## 3. 固定 Worker 和测试窗口

B 在两台独立物理电脑上启动 Worker，保持自己的 data_dir 与 worker.id，分别注册一个唯一标签。D 与 B 核对物理设备、CPU 型号、逻辑处理器数、内存、系统、网络、slots、软件版本；A 提供测试时 Coordinator 的提交 SHA，B 提供 Worker 提交 SHA。Worker 名称或 `rtx5060` 标签不能替代硬件确认。

2026-11-14 的标签映射为：

| 条件 | Worker 名称 | 唯一标签 | slots |
| --- | --- | --- | ---: |
| single-1 | liusic-rtx5060 | liusic-pc | 2 |
| single-2 | wsl-jochi | bench-jochi | 4 |

每台设备自身的 slots 在单 Worker 与 pool 条件之间保持不变；异构设备不要求彼此 slots 相同。给已有 Worker 加标签时，先等待在途任务结束，沿用原启动参数和 data_dir，再增加 `--tags`；不要复制别人的 worker.id。

提前安排无其他任务的测试窗口，并暂停无关重负载和部署操作。正式脚本在每轮开始前检查：两个标签各匹配且仅匹配一个已注册 Worker、两者在线且支持 thumbnail、没有额外在线 thumbnail Worker、没有排队或运行中的作业、已选 Worker 的身份与配置未改变。这些是边界快照检查，不能阻止其他人在运行中提交任务，仍需团队协调。

如果提示 `Tag 'bench-jochi' ... found 0`，先让 B 确认该标签已重新注册；不要删掉单 Worker 条件绕过错误。多个历史注册记录共用一个标签也会被拒绝，应核对注册记录或换用唯一标签。

## 4. 跑一次正确性冒烟

```powershell
uv run python benchmarks/thumbnail/run_thumbnail_smoke.py --dataset-root ../test-data --run
```

默认服务为 `https://cef.huaruoji.com`，也可用 `--server` 指定。Token 从隐藏输入读取，或使用已配置的 `CEF_TOKEN` 环境变量；不要写入脚本、提交记录或文档。

脚本只提交一次 16 张图片，固定 `size=256, fmt=png`，下载 ZIP 后检查：

1. Job、workload、参数、成功数量正确；task_id、索引和输出清单无重复或遗漏。
2. 每个输出 SHA256 与 manifest 一致，PNG 可完整解码。
3. 输出尺寸与原图按 EXIF 转正、LANCZOS 缩放至长边 256 的参考结果一致，解码后的 RGB 像素一致。
4. 至少两个 Worker ID 各有通过校验的输出。

两台在线不保证小批次一定由两台共同处理；检查实际贡献。冒烟成功证明输出与协作路径可用，不能计算单机/多机加速比。

## 5. 跑三组性能对照

```powershell
uv run python benchmarks/thumbnail/run_thumbnail_benchmark.py --dataset-root ../test-data --run --tags liusic-pc bench-jochi
```

执行前确认标签映射；更换设备时替换两个标签并重新测全部条件。单作业默认超时 600 秒，可通过 `--timeout` 调整。每轮检查图片质量、期望 Worker 与实际贡献；失败则保留证据并停止，不自动挑掉坏轮次或补成功样本。

输出默认在仓库根目录的 `data/d-acceptance/benchmark-<时间>/`，冒烟在同级 `cli-smoke-<时间>/`。控制台打印完整路径。要换位置可传 `--output-root <目录>`；这只影响新运行，已有结果不会移动。

提交或等待超时后，先查已保存的 `created.json`、Job ID 和任务列表。请求超时可能已经创建任务；脚本不会自动重提，也不会自动取消远端作业。先解决原因，再以新的完整实验运行保留成功和失败记录。

## 6. 指标与证据

`P = 本轮验证通过且唯一的输出数 / end_to_end_s`，单位 images/s。计时从客户端提交请求前开始，到结果 ZIP 下载完成结束，包含输入上传、轮询等待、结果下载及期间客户端开销；本地像素校验在计时结束后进行。

每组用三轮吞吐的中位数，保留最小值与最大值。`η = P_pool / (P_1 + P_2)`，`S = P_pool / max(P_1, P_2)`。两条单 Worker 基线也经过同一服务，因此这是有限批次的服务吞吐；`P_1 + P_2` 是经验参照，不是调度算法的严格理论上界。

额外保存提交请求耗时、提交至观察到终态耗时、下载耗时、服务端创建至完成耗时。前两项有包含关系，不能全部相加；`end_to_end_s = submit_to_terminal_s + download_s`。服务端时长是另一时钟上的观察窗口，不与客户端分项直接相加。

每约 5 秒请求一次最近 60 秒的心跳序列，按 Worker/时间戳去重，再限于该作业服务端 `created_at..finished_at` 窗口。均值是采样点算术平均，保留样本数和首末时间，缺失 GPU 数据保持 null。它描述整台设备，不能当成 thumbnail 进程占用或 GPU 算力证明。

本地保留 `experiment.json`、输入指纹、逐轮 Job/manifest/ZIP、`verification.json`、`round.json` 和资源采样。`summary.json` 与自动生成 `report.md` 是初步汇总，人工报告还需解释限制与瓶颈。分享时按本仓库 [精简证据](evidence/thumbnail-2026-11-14.json) 的方式挑选配置、计时、计数、Job ID 和 SHA256；去除 Token、主机名和个人绝对路径，原图/ZIP/完整日志不进 Git。

下一步独立安排真实 Worker 中断恢复、损坏输入和网页演示验收；这些异常实验不混入正常性能轮次。若扩展 infer 或 Blender，先固定模型/场景与质量判定，再分别设计实验，不能直接套用当前 PNG 校验器。
