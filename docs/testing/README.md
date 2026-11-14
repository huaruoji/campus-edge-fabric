# D：CPU / GPU 测试步骤与报告

CPU thumbnail 与 GPU infer 共用这个交付入口。组员从这里查看方法、实测结果和可核算的证据，原始运行目录保留在测试者本地。

| 实验 | Procedure | Report | 精简证据 | 复跑脚本 |
| --- | --- | --- | --- | --- |
| CPU：thumbnail 单机/双机 | [测试步骤](thumbnail-procedure.md) | [2026-11-14 报告](thumbnail-report-2026-11-14.md) | [JSON](evidence/thumbnail-2026-11-14.json) | [benchmarks/thumbnail](../../benchmarks/thumbnail/) |
| 双 GPU：infer 单机/双机诊断对照 | [测试步骤](infer-procedure.md) | [2026-11-14 报告与图表](infer-2026-11-14/report.md) | [JSON](infer-2026-11-14/evidence.json) | [benchmarks/infer](../../benchmarks/infer/) |

CPU thumbnail：100 张 BSDS300 图片，三条件各三次，900/900 个正式输出全部通过参考尺寸与逐像素校验。双 Worker 中位吞吐 **3.3055 images/s**，比最快单 Worker 提高 **26.17%**。

双 GPU infer：8/32/110 张 COCO 图片，三条件各三次，共 **27/27 轮**。110 张时 jochi / liusic / pool 的整批耗时中位数为 **70.08 / 79.05 / 37.50 秒**，双机等待时间比最快单机减少 **46.48%**。1350 个正式输出中 **1260 个**通过固定 CPU 数值参考；90 个未通过项均来自 jochi，涉及 15 张不同图片。诊断对照完成，数值一致性验收尚未全部通过；一次查询连接中断的恢复记录也保留在报告中。

两组数据的计时不同：thumbnail 包含逐轮上传，infer 复用已注册数据集、首次注册不计时。不能把两者直接拼成硬件性能排名，也不能把 infer 的数值参考通过率称为识别准确率。

输入来源、授权与固定划分见 [数据集说明](../../benchmarks/datasets/README.md)。物理设备身份、完整硬件配置和测试时远端部署提交仍需补充。Blender 与真实 Worker 断线恢复未在这两组实验中验收。

Git 收录文字、脚本、最终图表和筛选后的 JSON；不上传原图、模型、结果 ZIP、逐任务日志或凭据。`data/d-acceptance/` 中的原始证据继续本地保留。
