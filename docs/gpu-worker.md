# NVIDIA GPU Worker：安装、启停、验收

这套配置启用 `thumbnail` 和 `infer`，推理模型固定为本地 YOLO11n。Windows 11 + RTX 5060 已完成真实线上验收，见[验证报告](gpu-validation-2026-11-14.md)。WSL/Linux 提供下列部署步骤，仍需在目标电脑执行验收，不能直接沿用第一台的结果。

## 先确认设备与身份

- 使用 NVIDIA 独立显卡、可工作的 NVIDIA 驱动和 Python 3.12（本次验证版本）。安装需要网络和数 GB 空间。
- Windows 可运行 `nvidia-smi`；找不到命令时使用 `& "$env:SystemRoot\System32\nvidia-smi.exe"`。
- 每台电脑使用不同名称和专属标签，例如第二台用 `jochi-gpu`；保留自己的 `worker.id`，不要复制别人的节点身份。
- 已有 Worker 时，先在它原来的终端 Ctrl+C，等待任务完成并退出，再启动 GPU 版。同一物理电脑不要同时运行旧 Worker 和新 Worker 来充当两台设备。
- 若要保留原节点身份，所有命令都指定原数据目录：Windows `-DataDir <原目录>`，Python `--data <原目录>`。旧 Worker 必须先停止。否则默认在仓库 `cef-gpu-data/` 创建新身份，旧身份会变成离线。

模型、输入、日志、状态、令牌不进入 Git。默认 `.venv-gpu/` 和 `cef-gpu-data/` 已加入忽略列表。自定义数据目录请放到仓库外。

## Windows：直接复制执行

在仓库根目录打开 PowerShell：

```powershell
git pull --ff-only origin main
# 首次安装：独立虚拟环境、GPU 运行库、Worker/CLI、模型下载和真实 CUDA 试跑
.\deploy\gpu-worker.ps1 -Action Install

# Token 隐藏输入，只保存在当前终端进程；不粘贴进脚本文件
$secret = Read-Host 'CEF Token' -AsSecureString
$env:CEF_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
$env:CEF_SERVER = 'https://cef.huaruoji.com'

# 首次验收启用一次性算子跟踪，节点名和标签换成自己的
.\deploy\gpu-worker.ps1 -Action Start -Name jochi-gpu -Tags jochi-gpu -Slots 2 -CollectProfile
.\deploy\gpu-worker.ps1 -Action Status

# 定向到这台电脑，24 个图片检测任务；Token 从环境读取
.\.venv-gpu\Scripts\python.exe scripts\verify_gpu_pool.py --tag jochi-gpu
Remove-Item Env:CEF_TOKEN
```

`Start` 后台隐藏运行，最多等待 90 秒确认注册；重复启动同一数据目录会显示现有 PID。未提前设置 Token 时，启动脚本也会隐藏输入地询问，但随后运行验证工具仍须在终端设置 Token。

日常管理：

```powershell
.\deploy\gpu-worker.ps1 -Action Status
.\deploy\gpu-worker.ps1 -Action Stop
# 等 Status 中 alive=false 后再重启。日常运行无需 CollectProfile。
.\deploy\gpu-worker.ps1 -Action Start -Server https://cef.huaruoji.com -Name jochi-gpu -Tags jochi-gpu
```

`Stop` 停止接单，保持心跳直到正在执行的任务结束，再退出；命令返回表示停止请求已写入，实际退出以 Status 为准。没有配置开机自动启动，重启电脑后需重新 Start。`-Device 0` 可指定 NVIDIA 设备编号，默认 0；`-Slots 1` 可降低本机并发。

如果 PowerShell 当前策略阻止本地脚本，可使用单次进程参数，例如 `powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\gpu-worker.ps1 -Action Status`，无需永久修改系统策略。

## WSL2 / Linux

在 WSL2 中先运行 `/usr/lib/wsl/lib/nvidia-smi`。WSL 使用 Windows 主机的 NVIDIA 驱动，不要在 WSL 内安装 Linux 显卡驱动；具体要求见 [NVIDIA WSL 官方说明](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)。普通 Linux 则应先有正常可用的本机 NVIDIA 驱动。

在仓库根目录运行，Python 需带 `venv` 模块：

```bash
git pull --ff-only origin main
python3 -m venv .venv-gpu
.venv-gpu/bin/python -m pip install --timeout 120 --retries 8 \
  -r deploy/gpu-requirements.txt \
  -e packages/cef-protocol -e packages/cef-worker -e packages/cef-client
.venv-gpu/bin/python deploy/gpu_worker.py setup-model
.venv-gpu/bin/python deploy/gpu_worker.py check

read -rsp 'CEF Token: ' CEF_TOKEN; echo
export CEF_TOKEN
export CEF_SERVER=https://cef.huaruoji.com
mkdir -p cef-gpu-data
nohup .venv-gpu/bin/python deploy/gpu_worker.py run \
  --name jochi-gpu --tags jochi-gpu --slots 2 \
  --profile-dir cef-gpu-data/profiles \
  > cef-gpu-data/stdout.log 2> cef-gpu-data/stderr.log < /dev/null &
.venv-gpu/bin/python deploy/gpu_worker.py status
# 等待 status 的 ready=true，再运行验证
.venv-gpu/bin/python scripts/verify_gpu_pool.py --tag jochi-gpu --allow-missing-process-sample
unset CEF_TOKEN

# 日常查看与优雅停止
.venv-gpu/bin/python deploy/gpu_worker.py status
.venv-gpu/bin/python deploy/gpu_worker.py stop
```

WSL 的 NVIDIA 进程/利用率查询可能受限，因此例子显式允许缺少进程样本；报告会如实记录 `gpu_compute_pid_observed=false`，不会伪造进程证据。该选项**不跳过** CUDA 算子跟踪、结果后端和 CPU 对照检查。在支持进程查询的原生 Windows/Linux 上省略此选项。

再次验收须先正常停止并带 `--profile-dir` 重启；日常启动可去掉该参数。每次验证生成独立输出目录，不自动重复提交已有 Job。

## 验收要看什么

默认验证文件写入 `cef-gpu-data/validation/<时间>/`，模型跟踪写入 `cef-gpu-data/profiles/`：

1. `job-detail.json`：24 个任务全部成功，目标 Worker ID 正确。
2. `verification.json`：每份输出都为 `CUDAExecutionProvider`，SHA-256 正确，检测到人和公交车；与独立 CPU 会话比较类别、数量、置信度和框坐标。
3. `kernel_provider_counts` / `cuda_op_counts`：实际 Worker 会话中有 CUDA 卷积等节点，`profile_model_runs` 至少覆盖本批次数。
4. `gpu-samples.json`：支持 NVML 查询时核对 Worker PID 与 NVIDIA 计算进程。仅有 GPU 包、标签或全机 GPU 利用率升高都不足以通过验收。

验证使用同一张公开图片的亮度、镜像变体，输入每张不超过 500KB，最多 24 张。用途是验证链路和真实 GPU 执行，不是模型精度评测或性能基准。少量形状操作和前后处理运行在 CPU 上是正常情况。

完成后工具请求结束一次性跟踪，Worker 继续后台接单。手动结束跟踪可以运行 Windows `-Action Profile` 或 Python `deploy/gpu_worker.py profile`。

## 配置与排错

- 默认数据目录中的 `stderr.log` 记录注册和接单；`gpu-runtime.json` 保存 PID、创建时间、执行后端、模型路径和就绪状态，不含 Token。
- 专用环境使用 `onnxruntime-gpu==1.23.2`、cuDNN `9.28.1.4`，CUDA 12 运行库由 pip extras 安装。不要在这个环境再安装 CPU 版 `onnxruntime`，也不要用旧版 `worker.ps1` 或 `uv sync --extra all` 覆盖它。
- `check` 不只列举 provider，还会真正执行一次 640×640 模型推理。缺驱动、缺模型或 CUDA 加载失败应先修复，不能继续报 GPU 验收通过。
- Windows 若出现 `cudnn_engines_tensor_ir64_9.dll` 加载错误，请使用本配置入口；它保留 DLL directory 句柄，并为 cuDNN 延迟加载的子库配置进程内路径。安装方法依据 [ONNX Runtime CUDA 官方文档](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)。
- 模型加载后拒绝纯 CPU 会话及运行时自动回退；卷积算法采用 `HEURISTIC`，关闭 TF32，避免首次 exhaustive 搜索太久并便于数值对照。
- 模型固定为[莱昂大学机器人组的 YOLO11n 动态 ONNX 导出](https://huggingface.co/unileon-robotics/YOLO11-ONNX)，revision `0e2a3f195ae62a25bc038ee9665b8e4ac5b98793`，源权重为 Ultralytics，发布者声明 AGPL-3.0。下载检查 SHA-256，校验失败保留原模型，不自动执行任何下载的 Python 代码。
- 模型哈希：`b1a053916c9d667d05a53bfb43764a0aabb11dee20795af75b8ce408760685d3`。默认只支持请求 `model=yolo11n`，其他模型名会被拒绝。
