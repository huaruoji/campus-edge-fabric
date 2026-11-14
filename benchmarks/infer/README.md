# Infer：单 Worker 与双 Worker 对照

与 CPU thumbnail 实验一起收录在 [D 的测试入口](../../docs/testing/README.md)。

- [2026-11-14 双 GPU 诊断报告、图表和精简证据](../../docs/testing/infer-2026-11-14/report.md)
- [Procedure：设备选择、固定输入、计时和质量标准](../../docs/testing/infer-procedure.md)
- [数据来源、授权与划分](../datasets/README.md)

本次 27 轮诊断对照已完成。110 张批次的 jochi / liusic / pool 耗时中位数为 70.08 / 79.05 / 37.50 秒。1350 个正式输出中 1260 个通过固定 CPU 数值参考，90 个未通过项均来自 jochi，涉及 15 张不同图片。一次查询连接中断后分段恢复；报告保留数值差异、恢复记录及所有轮次，不能当成完整数值一致性验收。

## 准备

在仓库根目录、已安装 cef-protocol、cef-worker、NumPy、Pillow 和 CPU ONNX Runtime 的 Python 环境运行。生成图表还需 Matplotlib。本次客户端环境版本见报告证据 JSON；本地 CPU 仅提供正确性参考，不作为第三台性能设备。

输入默认放在仓库相邻的 `../test-data/`，按数据集清单与划分核对 SHA256，每张 ≤500,000 字节。模型按 [GPU Worker 指南](../../docs/gpu-worker.md)取得，保存为本地 `data/d-acceptance/models/yolo11n.onnx`，固定 SHA256：

```text
b1a053916c9d667d05a53bfb43764a0aabb11dee20795af75b8ce408760685d3
```

```powershell
python benchmarks/infer/prepare_reference.py
python benchmarks/infer/check_gpu_helpers.py
python benchmarks/infer/run_gpu_benchmark.py --auto-workers
```

参考输出保存为 `data/d-acceptance/infer-reference/reference.json`。固定参数为 `model=yolo11n, imgsz=640, conf=0.25, max_det=100`；置信度容差 0.002、框坐标容差 0.3 像素，检测数量与类别一致，使用一对一匹配处理排序差异。COCO128 是训练子集，本实验不测 mAP 或泛化精度。

## 运行

```powershell
python benchmarks/check_infer_workers.py
python benchmarks/infer/run_gpu_benchmark.py --run --auto-workers
```

Token 通过终端隐藏输入，也可读取当前进程 `CEF_TOKEN`；不保存到文件。只有 `--run` 才会提交。默认服务为 `https://cef.huaruoji.com`，可通过 `--server` 或 `CEF_SERVER` 修改。

自动模式选择至多两台在线、infer 可用且上报同一固定模型哈希的 Worker，优先声明 CUDA 的设备。每个作业用 `worker_ids` 限定执行范围，其他节点不会加入。每台先跑 8 张冒烟确认实际 CPU/CUDA provider，之后固定映射；配置或后端变化即停止。只有一台兼容设备时只跑冒烟，不生成双机结论。

正式设计为 8/32/110 张 × 单机 1/单机 2/pool × 3 次，共 27 个正式作业、1350 个输出。另有 2 个冒烟和 36 个预热作业。pool 前分别预热两台，输入组注册为 dataset 后复用，首次注册不计时。主要时间为提交已注册数据集的作业至下载结果 ZIP 完成；正确性校验在计时之外。

严格模式遇到数值不一致即停止。若要调查差异覆盖面，可显式使用诊断模式：

```powershell
python benchmarks/infer/run_gpu_benchmark.py --run --auto-workers --record-numerical-mismatches
```

诊断模式保持原容差，记录每张通过/未通过状态，只把通过项计入有效吞吐，保留整轮全部时间。文件结构、哈希、provider、配置或作业失败仍停止。结果目录前缀为 `diagnostic-auto-infer-`，必须同时报告数值通过率。

固定标签模式也保留：`--tags TAG1 TAG2 --providers cuda cuda` 或 `--providers cpu cuda`。这里的 provider 参数只指定校验预期，不改变远端环境；该旧模式要求恰好两台在线 infer Worker，且逐轮上传输入，计时不可与自动 dataset 模式混算。

## 中断与恢复

查询旧任务：`python benchmarks/check_infer_workers.py --job JOB_ID`。作业提交从不盲目重试；客户端断开不代表服务端作业停止。

```powershell
python benchmarks/infer/run_gpu_benchmark.py --run --resume data/d-acceptance/diagnostic-auto-infer-<时间>
```

恢复入口先离线重验已有结果和计划前缀，再核对原设备配置、查询并取回中断作业，重新冒烟及预热，只补剩余轮次。已完成的正式轮次不重跑。中断的旧计时不纳入性能，原始证据移入同一运行的 `continuations/<时间>/excluded/`，报告明确记录分段运行。非传输错误需要先调查，不通过该入口自动恢复。

只读 GET 遇到传输异常最多尝试四次；重试时间仍在当轮计时内，并保存记录。POST 提交不自动重试。中断作业仍在运行时先等待，不重复提交。

## 整理结果

```powershell
python benchmarks/infer/curate_report.py data/d-acceptance/diagnostic-auto-infer-<时间>
```

整理程序独立重验 65 个主实验 ZIP 及恢复/排除作业，重算指标，在运行目录的 `review/` 下生成中文报告、精简 JSON、耗时图与有效吞吐图（PNG/SVG）。未完成运行仅可显式用 `--partial` 生成带标记的阶段报告，不补齐缺失数据。

`diagnose_numerics.py` 可离线重算一个已保存作业的 CPU 结果，并比较现有远端输出；`--pillow-path` 允许使用另一份已有 Pillow 安装。诊断证据单独保存，不修改参考或历史判定。

Git 仅收录方法、脚本、最终报告、图表和精简证据。原图、模型、原始 ZIP、逐任务日志及凭据不上传。测量期间不要推送 main：该分支会触发 Coordinator 自动部署。
