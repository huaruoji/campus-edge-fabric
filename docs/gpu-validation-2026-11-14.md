# RTX 5060 本机 GPU 推理验收（2026-11-14）

Windows 11 上的 NVIDIA GeForce RTX 5060 Laptop GPU 已通过线上推理验收：**24 个任务全部成功、0 失败**。另一台 WSL 设备当时只启用 thumbnail，本次结果不代表它的 GPU 已配置。

## 运行配置与任务

| 项目 | 实测值 |
| --- | --- |
| Worker | `liusic-rtx5060` |
| Worker ID | `w_01M7CG4DBV3EC811G3YY9CQ6V4` |
| Job ID | `job_01M7CK6EQX8CJ1RW98K4KFK8VP` |
| GPU / 驱动 | RTX 5060 Laptop GPU，8151 MiB，device 0；596.13 |
| ONNX Runtime | GPU 1.23.2 |
| CUDA / cuDNN | CUDA 12.9 运行库系列 / cuDNN 9.28.1.4 |
| 模型 | YOLO11n FP32 动态 ONNX |
| 参数 | `imgsz=640, conf=0.25, max_det=100` |
| 并发 | 2 槽位 |
| CUDA 选项 | `cudnn_conv_algo_search=HEURISTIC, use_tf32=0` |

输入为 Ultralytics 公开公交车图片的 24 个亮度/镜像变体，JPEG 质量 85、长边最多 1024，每张小于 500KB，总上传 5,438,126 字节。没有使用私人图片。

## 验收证据

- 24 份输出均记录 `CUDAExecutionProvider`，且 Worker ID 均为目标节点。
- 输出 SHA-256、图片尺寸、人员和公交车检测均通过检查；与独立 CPU 会话比较，类别和检测数量一致，置信度误差小于 0.002，框坐标误差不超过 0.3 像素。
- **处理该线上批次的 Worker 会话**记录了 24 次 `model_run`、7,248 条 CUDA 节点事件，其中 2,112 次 Conv、48 次 FusedMatMul。
- 同一跟踪还记录了 888 条 CPU 节点事件，涉及 Gather、Concat、Unsqueeze、Mul；不宣称全流程每一步都在 GPU 上。
- NVIDIA 计算进程采样中的 PID **31560** 与 Worker 运行时 PID 相符，关联到 RTX 5060。WDDM 未提供该进程独占显存值，未据此作推断。
- 完成后两台节点在线且空闲，本机 GPU 负载指标已正常上报，GPU Worker 保留后台运行。

仓库中的[验证摘要](validation/gpu-2026-11-14/summary.json)包含逐张检查、算子计数和模型哈希；[原始算子跟踪（gzip）](validation/gpu-2026-11-14/worker-ort.json.gz)可解压为 Chrome trace JSON；[结果包](validation/gpu-2026-11-14/result.zip)包含服务端清单与检测结果。摘要同时保存原始证据文件哈希，可用于核对。原始显卡采样和本地状态文件留在验收电脑，仓库不包含个人绝对路径或凭据。

本次证明目标设备真实使用独立显卡执行任务，不构成模型精度测试、GPU 加速比或双机吞吐量结论。

## 脚本整理后的复验

本次最初使用本机专用启动器。仓库版已将路径、节点名、标签、显卡编号改为参数，提供 [Windows 启停脚本](../deploy/gpu-worker.ps1)、[跨平台入口](../deploy/gpu_worker.py)和[批量验收工具](../scripts/verify_gpu_pool.py)。新工具的复验结果单独记录在 [portable-smoke.json](validation/gpu-2026-11-14/portable-smoke.json)，不混入原始线上 24 个任务的结果。

完整安装、迁移旧节点、启停及 WSL 操作说明见 [GPU Worker 部署指南](gpu-worker.md)。第二台设备应按该指南独立验收。
