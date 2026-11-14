# CEF 仪表盘（web/）

Vite + React 18 + TypeScript 单页仪表盘，手写 CSS（无 UI 库、无 Tailwind）。构建产物由
Coordinator 直接托管：`cef-coord serve` 后打开 http://127.0.0.1:8080/ 。

## 目录结构

`web/`
├── index.html                 # 单页入口
├── vite.config.ts             # base "/"，outDir → packages/cef-coordinator/src/cef_coordinator/web
├── tsconfig.json              # strict + noUnusedLocals
└── src/
    ├── main.tsx               # ReactDOM 挂载 + ErrorBoundary
    ├── App.tsx                # 连接状态、轮询编排、hash 路由（#/devices #/submit #/jobs #/jobs/<id>）
    ├── api.ts                 # 全部接口类型 + CefApiError + createApiClient（唯一出网入口）
    ├── format.ts              # 时间/耗时/百分比/schema 默认值等纯函数
    ├── styles.css             # 深色主题 + 响应式（<720px 单列）
    ├── hooks/usePolling.ts    # 轮询（失败保留旧数据、AbortController、可手动 refresh）
    └── components/
        ├── ConnectionBar.tsx  # 地址 + Token（localStorage）+ 健康状态
        ├── StatsStrip.tsx     # tasks/min、p50、在线设备、任务总览、bottleneck
        ├── WorkerPanel.tsx    # 设备卡片（离线灰显、CPU/内存/GPU 条形）
        ├── JobSubmitForm.tsx  # workload + params(JSON) + 文件多选 + required_tag → multipart
        ├── JobList.tsx        # 任务列表（状态筛选、进度）
        ├── JobDetail.tsx      # 进度/ETA/per_worker 分解/任务明细分页/下载结果
        ├── ErrorBoundary.tsx  # 渲染兜底，避免白屏
        └── ui.tsx             # Badge/Bar/EmptyBox/ErrorBox/Section/Pagination

## 开发与构建

```bash
cd web
npm install
npm run dev      # http://127.0.0.1:5173 ，/api 代理到 127.0.0.1:8080
npm run build    # tsc --noEmit && vite build
```

构建直接输出到 `packages/cef-coordinator/src/cef_coordinator/web/`（`emptyOutDir: true`，
index.html 引用 `/assets/...` 绝对路径），不需要额外拷贝步骤。

## 接口约定与实现假设

- 严格按 `docs/api.md` 调 `/api/v1`；所有请求带 `Authorization: Bearer <token>`，
  默认地址为 `window.location.origin`，Token/地址存 localStorage。
- `/api/v1/health` 在后端免鉴权：Token 是否有效由需要鉴权的接口（workers/stats/jobs）判断，
  401 时顶部给出「认证失败」横幅。
- `/workloads` 404/500/连接失败时回退内置列表 thumbnail / infer / blender，并显示黄色提示；
  参数框在 schema 可用时按 `params_schema` 预填默认值。
- `/stats` 同时兼容契约里的 `jobs: {...}` 与当前实现的扁平字段
  （`jobs_total / jobs_running / tasks_succeeded / tasks_failed / tasks_queued`）。
- `per_worker.share`（0~1，成功任务占比）优先用于「占比」列；缺失时退化为按
  `total_duration_s` 计算耗时占比。
- 任务明细里 `worker` 是 worker_id，展示优先用 `worker_name`。
- 文件顺序与 `spec.inputs` 对应；batch 模式下每个文件一个 Task；blender 单文件按 `params.frames` 拆帧。
- 轮询：设备 3s、统计 5s、任务 5s、任务详情 2s；接口失败保留上一次成功数据并在面板内提示。
- 结果下载走 `fetch + blob`（浏览器 a 标签无法带 Authorization 头），文件名优先取 Content-Disposition。

## 已验证

- `npm install && npm run build` 通过（tsc strict 无错误）。
- 用真实 Coordinator（隔离 /tmp 数据目录）+ headless Chrome 跑通：设备卡片/离线灰显、统计条、
  提交 multipart、任务列表、详情 per_worker 与分页、结果 zip 下载、Token 错误、服务不可达、
  接口 404 空状态、无未捕获异常；390px 宽度无横向溢出。
