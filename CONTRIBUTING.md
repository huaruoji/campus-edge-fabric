# 协作约定

## 分支

- `main` 永远可跑（CI 绿）。直接 push 也行，但**动协议/数据库 schema 的改动先发 PR**。
- 功能分支：`feat/<scope>-<短描述>`，例如 `feat/worker-blender-adapter`。
- 各自负责的目录见 README 第 6 节；跨目录改动先群里说一声。

## 提交信息

```
<type>(<scope>): <中文一句话>
```
`type` ∈ `feat|fix|docs|test|chore|perf`；`scope` ∈ `coordinator|worker|protocol|web|cli|deploy|bench|docs`。

## 接口改动流程（重要）

1. 先改 `docs/api.md` + `packages/cef-protocol`（模型/枚举/参数）
2. 再改 coordinator，最后改 worker/client
3. 跑 `uv run pytest -q`，绿了再合并

不要在 worker 里直接写 `requests.post("/api/v1/...", raw_dict)`——一律走 `cef_protocol` 的模型。

## 本地起环境

```bash
uv sync --all-packages
uv run pytest -q
uv run cef-coord serve --port 8080 --data ./cef-data
# 另一个终端
uv run cef-worker run --server http://127.0.0.1:8080 --token <打印出来的 token> --workloads thumbnail
```

## 一天赛程（时间盒）

| 时间 | 目标 |
|---|---|
| 0:00–1:00 | 全部设备跑通 `thumbnail` 端到端（今天就能完成） |
| 1:00–3:00 | 选定主 workload（infer 或 blender）跑通真实生产路径 |
| 3:00–5:00 | `benchmarks/bench.py` 出 P_i / P_pool / η / S 第一版数据 |
| 5:00–6:30 | 仪表盘可演示：设备状态、进度、按设备分解、下载结果 |
| 6:30–7:30 | 断线/重试演示排练 + 产品故事与成本页 |
| 7:30+ | 冻结功能，只修 bug，准备现场 |
