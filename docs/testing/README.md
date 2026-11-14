# D：测试步骤与验收报告

这里是 D 的固定交付入口。原始运行目录留在本地，组员从这里查看方法、结论和可核算的证据。

| 内容 | 入口 |
| --- | --- |
| Procedure：环境、数据、冒烟、单机/双 Worker 对照、失败处理 | [thumbnail-procedure.md](thumbnail-procedure.md) |
| Report：2026-11-14 实测结果、计时分析、限制与下一步 | [thumbnail-report-2026-11-14.md](thumbnail-report-2026-11-14.md) |
| 精简证据：实验配置、输入指纹、18 轮摘要、Job ID、原始文件指纹 | [thumbnail-2026-11-14.json](evidence/thumbnail-2026-11-14.json) |
| 可复跑的脚本与离线检查 | [benchmarks/thumbnail](../../benchmarks/thumbnail/) |
| 三类测试资料的来源、授权与固定划分 | [benchmarks/datasets](../../benchmarks/datasets/README.md) |

本次正式测量：100 张 BSDS300 图片，thumbnail / PNG / 长边 256；两个单 Worker 条件和一个双 Worker 条件，各重复三次。双 Worker 中位吞吐 **3.3055 images/s**，相对最快单 Worker **1.262×（提高 26.17%）**，按单 Worker 吞吐之和计算的聚合效率 **88.26%**。900 个正式输出和 144 个预热输出全部通过离线复核。

上述指标是固定批次的端到端服务吞吐。物理设备确认、完整硬件配置和测试时服务端代码版本仍需补齐；infer、Blender 与真实断线恢复未在本轮验收。

Git 仅收录文字、脚本和筛选后的 JSON；没有上传原图、结果 ZIP、逐任务日志、Token、主机名或个人绝对路径。`data/d-acceptance/` 中的原始证据继续由测试者本地保留；不要用 `git add -f data/` 整包上传。
