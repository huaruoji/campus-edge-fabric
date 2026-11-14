# Coordinator / Worker 联调记录（2026-11-14）

工作归属、阶段完成情况及 A/B/C/D 后续任务见 [技术分工与交接表](team-handoff.md)。本记录描述本轮验证，不替代真实双机和最终交付版本的验收。

## 范围

从 main 的 `ae06e05` 开始检查，期间同步至 `5064b96`，保留设备身份持久化、设备删除和服务端 agent 更新。测试环境为 Windows / Python 3.12，本机真实 loopback HTTP Coordinator，两个独立 CLI Worker 进程，Pillow 执行 thumbnail。

这是本机完整软件链路验证，不是两台物理设备验收，不作为吞吐提升证据。没有使用线上业务实例或生产凭据。infer / blender 需要额外模型、运行时和设备，本次没有执行它们的计算任务；新增聊天 agent 和网页视觉交互也不在本次验收范围。

## 已复现并修复

1. **退出期间丢租约。** Worker 停止轮询时原本也停止心跳；在途任务执行超过 TTL，Coordinator 将其重排，最终结果收到 `409 stale_attempt`。现在轮询与心跳使用独立停止事件：先停止领取，等待执行与结果提交，再停止并等待心跳线程，最后 drain。
2. **忽略服务端周期。** 原本注册返回的 heartbeat/poll interval 没有用于运行循环。现在采用注册响应中的周期，重注册也更新；drain/stop 时不再产生新的领取容量。

原实现用 TTL=2 秒、4 秒慢任务复现：图像实际生成，但结果被拒绝，Job 留在 running。修复后，同样的真实图像任务在正常运行及中途请求退出两种情况均成功，attempt 保持 1。延迟只用于故障回归测试，不属于生产 workload。

## 验证场景

| 场景 | 核验内容 |
|---|---|
| 双 CLI Worker | 注册 → 上传 16 张 JPEG → 分发 → 输入下载与校验 → 实际缩图 → multipart 回传 → ZIP 下载 |
| 输出验证 | 16 个输出可解码，尺寸 64×32，输出 SHA256 与 manifest 一致，两 Worker 均有有效处理记录 |
| 部分失败 | 一张正常图、一张损坏图：partial，成功 1，失败 1 |
| 租约丢失 | 测试客户端领取后不发心跳，替代 Worker 在回收后执行，attempt=2；旧结果 409，不重复计数 |
| 慢任务与退出 | 任务时间超过 TTL，持续运行及中途请求停止均保持心跳，完成且不重试 |
| 声明式 CLI | `job submit --spec` → `job watch` → `job result`，结果包包含成功 manifest |
| 设备重启 | 使用同一 data_dir 重启 CLI Worker，复用 worker.id，节点数量仍为 1，重启后任务成功 |
| 契约与调度回归 | 原有 fencing、重试、错误封装、能力匹配、零容量、drain 和共享 Schema 快照检查 |

## 复现

```bash
uv sync --all-packages --extra thumbnail
uv run pytest packages/cef-coordinator/tests/test_live_pool.py -q --timeout=45
uv run pytest -q --timeout=45
uv run ruff check .
```

CI 同步安装 thumbnail extra，避免真实图像测试缺少 Pillow。测试自行创建临时输入、随机临时凭据、临时目录及本机端口，结束后停止自己创建的进程与服务。隔离环境若阻止本机 socket，需允许本机 HTTP/异步事件循环再执行。

回归代码：`packages/cef-coordinator/tests/test_live_pool.py`。测试中的延迟适配器只在测试进程注入；没有添加任何网络下发任意代码或命令的能力。
