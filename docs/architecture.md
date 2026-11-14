# 设计说明（面试/评委版）

## 角色

```
  提交端 (Web / cef CLI / Python SDK)          ┌──────────────┐
            │  POST /api/v1/jobs               │  SQLite      │
            ▼                                  │  jobs/tasks  │
   ┌──────────────────────┐   lease/poll  ┌───►│  workers     │
   │  Coordinator         │◄──────────────┤    │  leases      │
   │  FastAPI + scheduler │   heartbeat   │    └──────────────┘
   └──────────┬───────────┘   complete    │
              │ 静态仪表盘                  │
              ▼                            │
      judge / user            ┌────────────┴───────────┐
                              │ Worker Agent (每台设备) │
                              │ psutil/nvidia-smi 采样  │
                              │ workload adapter 执行   │
                              └────────────────────────┘
```

Coordinator 不主动连 Worker：所有连接都是设备主动出站，因此不需要开放个人电脑端口、不需要 SSH 私钥。

## 任务生命周期

```
queued ──claim(attempt+1)──▶ leased ──complete──▶ succeeded
  ▲                              │
  └──lease expired / fail ───────┘  （attempt ≥ max_attempts → failed）
```

三个不变量：

1. **只有手里租约仍有效的 Worker 能提交结果**（`attempt` 与 `worker_id` 都要匹配）。
2. **同一 attempt 只能被记一次成功**（UPDATE 带 `WHERE state='leased'`，rowcount=0 即拒绝）。
3. **任务不会永久卡住**：租约到期由 reaper 重排，不依赖 Worker 发"我挂了"的消息。

## 调度：为什么 Pull

Push 需要服务端知道每台设备"现在有多忙"——但这些设备是别人正在用的笔记本，随时会休眠、被占用、掉网。
Pull 把这些不确定性推给设备自己：有空闲就跑来要一批，没有就不来。服务端只做两件事：发租约、收结果。

代价是**批量**：一次只发一个任务，小任务会被 RTT 拖垮；一次发太多，慢设备会长时间占着任务。
所以用 `target_lease_s / EWMA(单任务耗时)` 算批量（默认目标 30 s 一批），再用 `max_batch` 和实时 CPU/GPU 负载封顶。
这就是 "Idle-Aware Adaptive Pull"。

## 数据与文件

- 输入/输出都走内容寻址存储（sha256），天然去重、可校验（Worker 下载后校验 sha，坏包直接拒跑）。
- 任务结果的 zip 是现拼的：`manifest.json`（每个任务的设备、attempt、耗时、输出 sha）+ `outputs/`。
- `manifest` 就是"各设备有效工作量"的原始记录，也是防造假的关键：每条都能对到具体文件的 sha256。

## 失败场景

| 场景 | 行为 |
|---|---|
| Worker 断网 | 心跳停止 → 显示 offline；租约到期 → 任务重排 |
| Worker 崩溃后恢复 | 旧租约已过期，提交结果被 409 拒绝（不会重复计数） |
| 设备被用户抢走 | 心跳报告高 CPU/GPU → 不再领新任务，只跑完手上的 |
| Ctrl-C 退出 | 调 `/drain`：释放未开始的租约，等待在跑的任务完成 |
| 任务本身报错 | `retryable` 决定重排或直接判失败，避免死循环重试 |

## 已知边界

- 单个 Coordinator 进程 + 单机 SQLite：这是为"一个队伍 5~10 台设备"选的规模，不是为校园级多租户。
- 没有任务级 checkpoint：大帧渲染中断就是重跑整帧（诚实说明，不假装有自动迁移）。
- Token 是共享密钥，LAN/Tailscale 内使用；要上公网必须加 TLS 与按角色的鉴权。
