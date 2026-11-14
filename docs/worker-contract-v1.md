# Coordinator ↔ Worker 固定契约 v1

本契约针对现有 CEF 实现，供两端并行开发。协议来源是 `cef_protocol.models`，人工语义在本文与 `api.md`，模型快照在 `worker-schema-v1.json`。修改路径、必填字段、单位、状态或错误语义必须先发 PR，并同时更新模型、快照、文档与测试。允许新增可选字段，接收方忽略未知字段；禁止两端私自另起 raw JSON 协议。

## 1. 固定约定

- 基地址 `http://<coordinator>:8080`，接口前缀 `/api/v1`。
- 所有 Worker 接口与 blob 下载携带 `Authorization: Bearer <token>`。
- 当前为共享 token 的可信节点模型，没有用户/设备独立授权；不要把 worker_id 当成身份凭据。不适合互不信任的多租户。
- JSON UTF-8；时间为 Unix 秒（浮点），`duration_s` 为秒，`idx` 从 0 开始。不要混用早期计划中的毫秒、ISO 时间和 `/worker/*` 路径。
- ID 不透明；任务 attempt 首次领取为 1，每次重新领取才递增。排回队列本身不递增。
- 单个 worker_id 只能由一个活跃 agent 使用，不能克隆给两台设备；agent 将 ID 保存到 data_dir/worker.id，重连和使用同一数据目录重启时复用 ID。没有保存 ID 的新数据目录注册为新设备。
- 当前 workload 为 `thumbnail / infer / blender`，不是早期 OCR 草案。

## 2. 路径与模型（默认均为 200）

| 方法与路径 | 请求模型/编码 | 响应模型与关键语义 |
|---|---|---|
| POST `/workers/register` | RegisterRequest JSON | RegisterResponse：ID、TTL、心跳间隔、轮询间隔 |
| POST `/workers/{worker_id}/heartbeat` | HeartbeatRequest JSON | HeartbeatResponse：TTL、poll_now、drain |
| POST `/workers/{worker_id}/poll` | PollRequest JSON | PollResponse：leases 数组、suggested_capacity |
| GET `/blobs/{sha}` | 无 body | 原始字节，Worker 校验 SHA256 |
| POST `/workers/{worker_id}/tasks/{task_id}/complete` | 表单 meta 为 CompleteMeta JSON 字符串，可重复 files 上传文件 | CompleteResponse：ok=true, credited=true, reason=null |
| POST `/workers/{worker_id}/tasks/{task_id}/fail` | FailRequest JSON | FailResponse：requeued、attempts_left |
| POST `/workers/{worker_id}/drain` | 无 body | **202** DrainResponse：worker_id、released |

完整必填、默认值、可空字段在 JSON Schema 快照。输出 `output` 是适配器元数据；`output.files` 由 Coordinator 根据上传文件覆盖生成。`output_names` 当前保留但未使用，名称来自 multipart 文件名。无文件 complete 允许以表单仅提交 meta。

## 3. 可直接对接的消息

注册：
```json
{"worker_id":null,"name":"pc-1","hostname":"pc-1","os":"Windows","cpu_count":8,"tags":["bench-pc1"],"slots":2,"max_batch":2,"workloads":[{"kind":"thumbnail","available":true,"version":"pillow","reason":null}],"meta":{}}
```

心跳：
```json
{"stats":{"cpu_pct":35.0,"mem_pct":50.0,"gpu_pct":null,"running":1},"leases":["task-id-from-poll"]}
```

轮询：
```json
{"capacity":1,"workloads":["thumbnail"],"tags":[]}
```

无任务仍为 200：
```json
{"leases":[],"suggested_capacity":2}
```

complete 的 meta 表单字段：
```json
{"attempt":1,"duration_s":0.25,"output":{"width":512,"height":512},"output_names":[]}
```

失败：
```json
{"attempt":1,"error":"adapter failed to decode input","retryable":false}
```

## 4. 边界行为

### 注册、轮询与能力

注册声明能力；poll.workloads 与注册时 available=true 的能力取交集。tags 以注册值为准，poll.tags 是保留字段，不覆盖注册标签。

`capacity` 范围 0—64；0 不领任务。它表示本次希望领取的数量，Worker 必须自行限制为空闲 slots，不能把 suggested_capacity 当成额外空闲槽位。draining/offline 节点不领新任务。没有匹配任务是 200 空数组，不能解释为故障。重复 poll 是新的领取操作，不是幂等重放。

### 心跳、租约与重试

心跳列出当前持有的 task_id，Coordinator 为仍属于该节点且仍为 leased 的任务续租。心跳丢失不会立即迁移，维护循环在租约到期后回收，受 max_attempts 限制。

v1 心跳不携带 attempt，不能区分同一 worker_id 的两个并行进程，因此禁止多进程共用 ID。租约 fencing 在 complete/fail 时核对 task、worker、attempt 和 leased 状态；TTL 是维护循环驱动回收，不承诺时钟刚过期的瞬间就拒绝结果。尚未被回收的过期租约仍可能被续租/完成；这是 v1 当前边界。

成功重复回传返回 **409 stale_attempt**，不会再次 credited；不是“重复仍 200”。如果第一次响应丢失，随后 409 不能证明第一次失败，应查询任务/manifest 确认结果。fail 的重发同样可能返回 409。

### drain

drain 立即释放该节点所有 leased 任务（包括已经开始执行的任务），返回释放数量；之后结果将被 fencing 拒绝。优雅退出先本地停止领取、保持心跳、等待本机任务提交完，再停止心跳并调用 drain。Agent 使用独立心跳生命周期，并采用注册响应中的心跳/轮询间隔；正常停止不提前丢失在途租约。强制结束进程、断网或适配器永久卡死仍不保证无损。未知 worker_id 的 drain 当前返回 202、released=0。

### 错误与恢复

统一正文：`{"error":{"code":"...","message":"..."}}`，不嵌套在 detail 下。

| HTTP | code | Worker 行为 |
|---|---|---|
| 401 | unauthorized | 检查凭据，避免快速循环 |
| 404 | not_found | 未知 worker 可重新注册；未知 blob/task 则记录具体失败 |
| 400 | invalid_params | meta JSON 无效，修正发送方，不盲重试 |
| 422 | invalid_params | 请求模型/表单参数无效，修正发送方 |
| 409 | stale_attempt | 放弃该旧尝试的提交，不继续计入本地有效完成 |
| 5xx/断网 | internal/传输错误 | 有间隔重试；不能假定请求未在服务端执行 |

API 的 succeeded 仅表示服务端接受了执行结果，不证明图像/推理/渲染质量合格；评测需另行检查输出。

## 5. 两端交付与验证

- Coordinator 负责人维护路由、错误封装、租约/计数、共享模型和快照。
- Worker 负责人使用 WorkerAPI；WorkerAPI 使用共享模型校验请求/响应，保留现有 agent 字典返回兼容性。
- 联调顺序：注册两台 → 上传任务 → poll → 下载并核验输入 → 本地处理 → complete → 验证 manifest → 验证 duplicate/old attempt、fail、capacity=0 和 drain。
- 运行 `uv sync --all-packages` 后执行 `uv run pytest -q`。
- 契约行为测试位于 `packages/cef-coordinator/tests/test_worker_contract.py`，通过真实 WorkerAPI 调用进程内 ASGI Coordinator；这不是两台物理设备的验收证据。
- 快照更新：`uv run python scripts/export_worker_schema.py`；CI 测试会比较共享模型生成值与已提交快照。
- 旧排期文件位于 `docs/team-plan/`，仅为历史计划，不具有接口约束力。
