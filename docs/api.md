# CEF API v1 — 接口契约（冻结版）

> 这是 Coordinator 与所有客户端（Web / CLI / SDK / Worker Agent）之间的**唯一**契约。
> Worker 对接的规范补充、边界行为与联调步骤见 [worker-contract-v1.md](worker-contract-v1.md)，机器可读模型见 [worker-schema-v1.json](worker-schema-v1.json)。
> 改接口先改这个文件 + `packages/cef-protocol`，再改实现。CI 会跑契约测试。

## 0. 约定

- Base URL: `http://<coordinator>:8080`
- 所有接口前缀 `/api/v1`，请求/响应体均为 JSON（除文件上传/下载）。
- 鉴权：`Authorization: Bearer <token>`。MVP 使用单一共享 token（`CEF_TOKEN`），
  不暴露无鉴权的公网端口；LAN / Tailscale 内使用。
- 时间：Unix epoch 秒（float）。ID：ULID 风格字符串（时间有序）。

## 1. 概念模型

```
Job (一次用户提交)  ──1:N──> Task (最小调度单元)
                                │
                                └─ 被某个 Worker 领取 → Lease(attempt)
```

- **Task** 是原子单位：一张图（infer/thumbnail）或一帧（blender）。
- **Lease** 是 `(task, worker, attempt)` 的租约；**attempt 是 fencing token**。
- 重复 / 迟到结果通过 fencing 丢弃，绝不重复计数。

状态机：

```
Task:   queued --claim--> leased --complete--> succeeded
                            │  └--fail(retryable)--> queued (下次 claim 时 attempt+1)
                            │  └--lease expired----> queued (下次 claim 时 attempt+1)  [reaper]
                            └--fail(final)/attempt>max--> failed
Job:    queued --> running --> succeeded | partial | failed | canceled
Worker: online <--> draining --> offline (heartbeat 超时)
```

## 2. 提交端（Web / CLI / SDK 共用）

### POST /api/v1/jobs
multipart/form-data：

| 字段 | 说明 |
|---|---|
| `spec` | JSON 字符串：`{workload, params, name?, priority?, required_tag?, inputs:[{name}]}` |
| `files` | 一个或多个 `files` 部分，顺序与 `inputs` 对应；也可以多个文件=多任务（batch 模式） |

```jsonc
// spec
{
  "workload": "thumbnail",          // thumbnail | infer | blender
  "params": {"size": 256},          // 由 workload 的 param model 校验
  "name": "demo-batch-01",
  "priority": 0,
  "required_tag": null,             // 只允许带该 tag 的 worker 领取（基准测试用）
  "inputs": [{"name": "a.jpg"}, {"name": "b.jpg"}]
}
```
Batch 约定：`inputs[i]` 对应第 i 个 `files` 部分 → 生成 i 个 Task。
Blender 特殊：`inputs=[{name:"scene.blend"}]` + `params.frames="1-120"` → 拆成 120 个 Task。

**201** `{job_id, total, state}`

### GET /api/v1/jobs?limit=50&state=running
`{jobs: [JobSummary]}`

### GET /api/v1/jobs/{job_id}
```jsonc
{
  "job_id": "...", "name": "demo", "workload": "thumbnail", "state": "running",
  "total": 20, "succeeded": 12, "failed": 0, "queued": 8,
  "created_at": 1763100000.0, "finished_at": null,
  "eta_s": 41.2,
  "per_worker": [{"worker_id": "w1", "name": "beelink", "completed": 8,
                  "mean_duration_s": 0.31, "total_duration_s": 2.48, "failed": 0}],
  "tasks": [{"task_id": "...", "idx": 0, "state": "succeeded", "worker": "beelink",
             "attempt": 1, "duration_s": 0.30}]
}
```
`per_worker` 就是评委要的「各设备有效处理记录」。

### GET /api/v1/jobs/{job_id}/result
`application/zip`：`manifest.json` + `outputs/<task_idx>_<name>` + `outputs/<...>.json`
manifest 含每个 task 的 `worker_id / attempt / duration_s / output_sha256`。

### GET /api/v1/jobs/{job_id}/manifest
同上 manifest 的 JSON（不打包）。

### POST /api/v1/jobs/{job_id}/cancel → 202

### GET /api/v1/workers
`{workers:[{worker_id,name,hostname,os,cpu_count,tags,slots,state,
  last_seen_s_ago,cpu_pct,mem_pct,gpu_pct,running,completed_total,
  mean_duration_s,throughput_per_min}]}`

### GET /api/v1/stats?window=300
`{now, jobs:{...}, tasks_per_min, p50_duration_s, workers:[{worker_id,util_series:[{ts,cpu_pct,gpu_pct}]}],
  bottleneck:"hint string"}` — 仪表盘和基准脚本共用。

### GET /api/v1/workloads
`{workloads:[{kind, available_on:[worker names], params_schema(JSON Schema), unit:"images|frames"}]}`

### GET /api/v1/health → `{ok:true, version, uptime_s, workers_online}`

## 3. Worker 端

### POST /api/v1/workers/register
```jsonc
{"worker_id": null,                    // 首次为 null，服务端分配；重连带上旧 id
 "name": "beelink-s12", "hostname": "...", "os": "linux/6.8", "cpu_count": 8,
 "tags": ["gpu"], "slots": 2, "max_batch": 4,
 "workloads": [{"kind": "thumbnail", "version": "pillow-11.0", "available": true},
               {"kind": "blender", "version": null, "available": false, "reason": "blender not found"}]}
```
→ `{worker_id, lease_ttl_s: 90, heartbeat_interval_s: 5, poll_interval_s: 1.0}`

### POST /api/v1/workers/{id}/heartbeat
`{"stats": {"cpu_pct": 37.2, "mem_pct": 55.0, "gpu_pct": null, "running": 2}, "leases": ["task_id", ...]}`
→ `{lease_ttl_s, poll_now: bool, drain: bool}`（`leases` 中的租约顺延；`drain=true` 时 worker 优雅退出）

### POST /api/v1/workers/{id}/poll
`{"capacity": 3, "workloads": ["thumbnail","infer"], "tags": []}`
→ `{"suggested_capacity": 1, "leases": [{"task_id","job_id","idx","attempt","workload","params",
   "inputs":[{"name","sha256","size","url"}],"lease_expires_at","lease_ttl_s"}]}`
`url` 是 `/api/v1/blobs/{sha256}` 相对路径，worker 直接用同一 token 拉取。
`capacity=0` 明确不领任务；无任务为 200 空 leases（不是 204）。非 online 节点不领任务；workloads 与注册的 available 能力求交集。tags 以注册信息为准。

### POST /api/v1/workers/{id}/tasks/{task_id}/complete
multipart：`meta` = JSON `{attempt, duration_s, output:{...}, output_names:[]}`，`files` = 输出文件。
→ 200 `{ok:true, credited:true}`；fencing 不匹配 → **409** `{"error":{"code":"stale_attempt","message":"result not credited"}}`（worker 丢弃，不重试）

### POST /api/v1/workers/{id}/tasks/{task_id}/fail
`{"attempt": 2, "error": "CUDA OOM", "retryable": true}` → `{requeued: true, attempts_left: 1}`

### POST /api/v1/workers/{id}/drain → 202 `{worker_id,released}`

立即释放该 worker 所有未完成租约；不区分已开始和未开始。优雅停止须先停止 poll、保持心跳并等待本机任务完成，再调用 drain。

### GET /api/v1/blobs/{sha256} → 原始字节（输入/输出文件下载）

## 4. 错误格式

`{"error": {"code": "stale_attempt|not_found|invalid_params|unauthorized|conflict", "message": "..."}}`

## 5. CLI（多客户端入口之一，与 HTTP 一一对应）

```bash
cef --server http://host:8080 --token T doctor
cef job submit --workload thumbnail --params '{"size":256}' images/*.jpg
cef job ls / cef job show <id> / cef job watch <id> / cef job result <id> -o out.zip
cef workers / cef stats
```
`cef_protocol.client.CefClient` 是同一套调用的 Python SDK（notebook 里可直接用）。

## 6. 内置 agent 与设备清理（v1 增补）

### POST /api/v1/agent/chat
自然语言 → 校验过的任务预览（不会自己提交）。

```jsonc
// request
{"messages": [{"role": "user", "content": "把这批图压成 512 的 webp"}],
 "context": {"files": [{"name": "a.jpg", "size": 231233}]}}

// response
{"reply": "已按 512/webp 准备好，确认后即可提交",
 "preview": {"workload": "thumbnail", "params": {"size": 512, "fmt": "webp", "quality": 80},
             "task_count": 1, "note": "将以界面已上传的 1 个文件为输入"},
 "tool_trace": [{"name": "preview_job", "ok": true, "summary": "..."}],
 "model": "gpt-5.4-mini"}
```

- 模型在服务端调用，key 只存在于 `/etc/cef/agent.env`，浏览器永远拿不到。
- 工具：`list_workloads / list_workers / pool_stats / get_job / recent_events / preview_job`。
- 未配置 key 时返回 **503** `agent_unavailable`。

### DELETE /api/v1/workers/{worker_id}
删除一台设备记录（清理仪表盘）。它会先把自己持有的未完成任务放回队列，不会丢任务。
**204** 成功 / **404** 不存在。
## 7. 共享对话、标签治理、任务可读化（v1 增补）

### GET /api/v1/chat/messages?room=pool&after=<created_at>&limit=200

共享对话记录（所有持 token 的人看到同一份）。`after` 是 `created_at` 浮点游标，返回升序增量；不传则返回最近 200 条。

```jsonc
{"messages": [{"id": "msg_…", "room": "pool", "role": "user|assistant",
  "author": "alic", "content": "把图压到 512", "trace": null, "preview": null,
  "job_id": null, "created_at": 1794644475.9}],
 "latest": 1794644475.9}
```

### POST /api/v1/agent/chat（行为升级）

请求体新增 `author`（昵称）与 `room`；服务端会把 user 消息和 assistant 回复都落库，响应新增
`user_message_id` / `assistant_message_id`。前端不需要再单独 POST 一条消息。

### DELETE /api/v1/chat/messages?older_than_days=7

裁剪历史，始终保留最新 200 条。返回 `{removed, older_than_days}`。

### GET /api/v1/tags

```jsonc
{"tags": [{"tag": "bench-jochi", "category": "bench", "total": 1, "online": 1}]}
```

`category` ∈ `role` / `hw` / `site` / `bench` / `free`。标签在服务端规范化：小写、下划线转连字符、
只允许 `a-z0-9.:-`、去重排序、最长 24 字符、最多 8 个。

### PATCH /api/v1/workers/{worker_id}

```jsonc
// 请求
{"tags": ["role:gpu", "hw:rtx5060"]}
// 响应
{"worker_id": "w_…", "tags": ["hw:rtx5060", "role:gpu"], "rejected_tags": []}
```

### GET /api/v1/jobs/{job_id}（新增字段）

| 字段 | 说明 |
|---|---|
| `params_text` | 人类可读参数摘要，如「缩略图，长边 512px，WEBP，质量 80」 |
| `input_summary` | 「12 个文件，共 3.4MB」（按 sha 去重，blender 只算一次 .blend） |
| `submitter` | 提交者昵称（`JobSpec.submitter`，可空） |
| `tasks[].inputs` / `tasks[].outputs` | `[{sha256, name, size, url}]`，url 指向 `/api/v1/blobs/{sha}` |
| `tasks[].summary` | 单任务摘要，如「900×600 → 512×341，WEBP 18KB」/「3 个目标」/「第 42 帧」 |

### GET /api/v1/blobs/{sha}（变更）

现在返回正确的 `Content-Type`（上传时记录，缺失则按文件名推断）并带 `Cache-Control: private, max-age=86400`。
仍然需要 `Authorization`；`<img>` 无法带 header，前端应 fetch 后转 objectURL。

### 提交者

`POST /api/v1/jobs` 的 spec 支持 `submitter` 字段；CLI 会用 `CEF_SUBMITTER` 或系统用户名兜底。MD
