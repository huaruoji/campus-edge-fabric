# 固定 thumbnail 验收脚本

完整操作见 [Procedure](../../docs/testing/thumbnail-procedure.md)，已完成实验见 [Report](../../docs/testing/thumbnail-report-2026-11-14.md)。

- `run_thumbnail_smoke.py`：16 张固定图片，经现有 `cef` CLI 提交，检查图片与至少两个 Worker 的实际贡献。
- `run_thumbnail_benchmark.py`：使用 `CefClient`，两个唯一标签对应两组单 Worker 基线，另测不限标签的双 Worker 条件；每组 3 次，每次先预热。
- `check_benchmark_helpers.py`：离线检查标签约束、资源采样窗口和三轮聚合规则。

从仓库根目录运行；默认只检查本地输入，只有 `--run` 才联系服务并提交任务。默认输入根目录是 `data/test-datasets/`，输出根目录是被 Git 忽略的 `data/d-acceptance/`，可分别用 `--dataset-root`、`--output-root` 指定其他位置。

```powershell
uv run python benchmarks/thumbnail/run_thumbnail_smoke.py --dataset-root ../test-data
uv run python benchmarks/thumbnail/run_thumbnail_benchmark.py --dataset-root ../test-data
uv run python benchmarks/thumbnail/check_benchmark_helpers.py
```

脚本只针对本次 `thumbnail / PNG / 256` 和两个 Worker 的设计。旧的通用 `benchmarks/bench.py` 与本次报告的计时、质量判定不同，不应混用其数值。

发布版迁移自本地已执行的脚本，仅调整仓库相对位置、输入/输出根目录选项和脚本指纹记录。历史运行时脚本位于忽略目录，不属于报告中的客户端代码提交；整理时对保留的原脚本计算的 SHA256 记录在精简证据的 `source_fingerprints.original_scripts` 中。发布版尚未重新进行线上性能测量。
