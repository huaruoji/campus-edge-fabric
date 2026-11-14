import { DetectionPreview } from "./DetectionPreview";
import { Fragment, useEffect, useState } from "react";
import type { ApiClient, BlobBrief } from "../api";
import { CefApiError, isAbortError } from "../api";
import { usePolling } from "../hooks/usePolling";
import {
  clampPct,
  formatDuration,
  formatEta,
  formatNumber,
  formatPct,
  formatUnix,
  isTerminalJobState,
  jobProgress,
  sharePct,
} from "../format";
import { workloadLabel } from "../presets";
import {
  EmptyBox,
  ErrorBox,
  LoadingLine,
  Pagination,
  Section,
  StateBadge,
  stateLabel,
  UpdatedInfo,
} from "./ui";
import "../chat.css";

/** 单页最多同时拉取的图片/JSON 数量。 */
const MAX_PARALLEL_ASSET_LOADS = 12;

const IMAGE_EXT = /\.(png|jpe?g|webp|gif|bmp|avif)$/i;

let activeAssetLoads = 0;
const assetLoadWaiters: Array<() => void> = [];

/** 简单的并发闸门：超过上限的加载排队，释放一个再放行一个。 */
function acquireAssetSlot(): Promise<() => void> {
  return new Promise((resolve) => {
    const grant = (): void => {
      activeAssetLoads += 1;
      let released = false;
      resolve(() => {
        if (released) return;
        released = true;
        activeAssetLoads -= 1;
        const next = assetLoadWaiters.shift();
        if (next) next();
      });
    };
    if (activeAssetLoads < MAX_PARALLEL_ASSET_LOADS) grant();
    else assetLoadWaiters.push(grant);
  });
}

function isImageAsset(asset: BlobBrief): boolean {
  return IMAGE_EXT.test(asset.name);
}

function isJsonAsset(asset: BlobBrief): boolean {
  return /\.json$/i.test(asset.name);
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function asPlainRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function previewJsonValue(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return value.length + " 项";
  if (typeof value === "object") return "对象";
  if (typeof value === "string") return value.length > 60 ? value.slice(0, 60) + "…" : value;
  return String(value);
}

interface Props {
  api: ApiClient;
  jobId: string;
  onBack: () => void;
  connectionError: CefApiError | null;
}

/**
 * 任务详情：GET /api/v1/jobs/{id}，2 秒轮询直到离开页面。
 * 首屏解释「这个任务在做什么」，任务明细行可按需展开输入/产物。
 * 图片走带 Authorization 的 fetch → objectURL，展开才拉，最多同时 12 张。
 */
export function JobDetail({ api, jobId, onBack, connectionError }: Props) {
  const poll = usePolling((signal) => api.getJob(jobId, signal), 2000, api.hasToken, [api, jobId]);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [downloading, setDownloading] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const job = poll.data;
  const tasks = job?.tasks ?? [];
  const progress = job ? jobProgress(job) : { done: 0, total: 0, pct: 0 };
  const terminal = job ? isTerminalJobState(job.state) : false;
  const repeatsConnectionError = poll.error instanceof CefApiError
    && poll.error.kind === connectionError?.kind && poll.error.code === connectionError?.code;

  useEffect(() => {
    const maxPage = Math.max(0, Math.ceil(tasks.length / pageSize) - 1);
    setPage((value) => Math.min(value, maxPage));
  }, [tasks.length, pageSize]);

  // 翻页时收起已展开的行，避免同时堆太多图片。
  useEffect(() => {
    setExpanded([]);
  }, [page, pageSize]);

  async function handleDownload() {
    setActionError(null);
    setNotice(null);
    setDownloading(true);
    try {
      const { blob, filename } = await api.downloadResult(jobId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
      setNotice("已开始下载 " + filename);
    } catch (cause) {
      setActionError(cause);
    } finally {
      setDownloading(false);
    }
  }

  async function handleAssetDownload(asset: BlobBrief) {
    setActionError(null);
    setNotice(null);
    try {
      const blob = await api.fetchBlob(asset.url);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = asset.name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
      setNotice("已开始下载 " + asset.name);
    } catch (cause) {
      setActionError(cause);
    }
  }

  async function handleCancel() {
    if (!job) return;
    if (!window.confirm("确认取消该任务？已完成的输出会保留。")) return;
    setActionError(null);
    setNotice(null);
    setCanceling(true);
    try {
      await api.cancelJob(jobId);
      setNotice("已发送取消请求，正在更新任务状态。");
      poll.refresh();
    } catch (cause) {
      setActionError(cause);
    } finally {
      setCanceling(false);
    }
  }

  function toggleTask(taskId: string) {
    setExpanded((previous) =>
      previous.includes(taskId) ? previous.filter((item) => item !== taskId) : [...previous, taskId]
    );
  }

  const completedTotal = job?.succeeded ?? job?.per_worker.reduce((sum, row) => sum + row.completed, 0);
  const pageTasks = tasks.slice(page * pageSize, page * pageSize + pageSize);
  const elapsedSeconds = job?.created_at
    ? Math.max(0, (job.finished_at ?? Date.now() / 1000) - job.created_at)
    : null;
  const deviceSeconds = job?.per_worker.reduce((sum, row) => sum + (row.total_duration_s ?? 0), 0) ?? 0;

  return (
    <div className="detail">
      <div className="detail-toolbar">
        <button type="button" className="btn btn--sm btn--ghost" onClick={onBack}>
          ← 返回任务列表
        </button>
        <span className="mono detail-id">{jobId}</span>
        <div className="detail-toolbar-actions">
          <UpdatedInfo updatedAt={poll.updatedAt} />
          <button type="button" className="btn btn--sm btn--ghost" onClick={poll.refresh}>
            刷新
          </button>
          <button
            type="button"
            className="btn btn--sm btn--primary"
            onClick={() => void handleDownload()}
            disabled={downloading || !job || !terminal}
            title={terminal ? "下载结果包" : "任务结束后才能下载结果包"}
          >
            {downloading ? "下载中…" : "下载结果"}
          </button>
          <button
            type="button"
            className="btn btn--sm btn--danger"
            onClick={() => void handleCancel()}
            disabled={canceling || !job || terminal}
          >
            {canceling ? "取消中…" : "取消任务"}
          </button>
        </div>
      </div>

      {poll.error && !repeatsConnectionError ? (
        <ErrorBox error={poll.error} onRetry={poll.refresh} compact={Boolean(job)} />
      ) : null}
      {actionError ? <ErrorBox error={actionError} onRetry={() => setActionError(null)} /> : null}
      {notice ? <div className="notice" role="status">{notice}</div> : null}

      {!job ? (
        poll.loading ? (
          <LoadingLine text="正在读取任务详情…" />
        ) : (
          <EmptyBox
            title="任务详情不可用"
            hint={
              repeatsConnectionError
                ? "请按上方提示检查连接，然后重新检测。"
                : "返回任务列表可以查看当前可用的任务。"
            }
          />
        )
      ) : (
        <>
          <Section
            title={job.name || "未命名任务"}
            subtitle={"创建于 " + formatUnix(job.created_at)}
            actions={<StateBadge state={job.state} kind="job" />}
          >
            <div className="detail-overview">
              <h3 className="detail-overview-title">这个任务在做什么</h3>
              <dl className="detail-overview-list">
                <div>
                  <dt>工作负载</dt>
                  <dd>
                    {workloadLabel(job.workload)}
                    {job.workload ? <span className="mono detail-kind">{job.workload}</span> : null}
                  </dd>
                </div>
                <div>
                  <dt>参数</dt>
                  <dd>{job.params_text || "后端没有提供参数描述"}</dd>
                </div>
                <div>
                  <dt>输入</dt>
                  <dd>{job.input_summary || "后端没有提供输入摘要"}</dd>
                </div>
                <div>
                  <dt>提交者</dt>
                  <dd>{job.submitter || "未知"}</dd>
                </div>
                {job.required_tag ? (
                  <div>
                    <dt>指定标签</dt>
                    <dd><span className="tag">{job.required_tag}</span></dd>
                  </div>
                ) : null}
                <div>
                  <dt>{terminal ? "总耗时" : "已运行"}</dt>
                  <dd>{formatDuration(elapsedSeconds)}</dd>
                </div>
                {deviceSeconds > 0 ? (
                  <div>
                    <dt>设备累计耗时</dt>
                    <dd>{formatDuration(deviceSeconds)}</dd>
                  </div>
                ) : null}
              </dl>
            </div>

            <div className="progress-block">
              <div className="progress progress--lg" aria-hidden="true">
                <span
                  className={"progress-fill progress-fill--" + job.state}
                  style={{ width: progress.pct + "%" }}
                />
              </div>
              <div className="progress-caption" role="status" aria-live="polite" aria-atomic="true">
                <span>
                  <span className="visually-hidden">{stateLabel(job.state, "job")}。已成功 {job.succeeded ?? 0} 个，失败 {job.failed ?? 0} 个。</span>
                  {progress.done} / {progress.total} 个任务已处理
                </span>
                <span>{formatPct(progress.pct)}</span>
              </div>
            </div>

            <dl className="detail-meta">
              <div className="kv">
                <dt>总任务</dt>
                <dd>{formatNumber(job.total)}</dd>
              </div>
              <div className="kv">
                <dt>成功</dt>
                <dd className="text-ok">{formatNumber(job.succeeded)}</dd>
              </div>
              <div className="kv">
                <dt>失败</dt>
                <dd className={job.failed ? "text-err" : undefined}>{formatNumber(job.failed)}</dd>
              </div>
              <div className="kv">
                <dt>排队中</dt>
                <dd>{formatNumber(job.queued)}</dd>
              </div>
              <div className="kv">
                <dt>预计剩余</dt>
                <dd>{terminal ? "—" : job.eta_s == null ? "等待估算" : formatEta(job.eta_s)}</dd>
              </div>
              <div className="kv">
                <dt>完成时间</dt>
                <dd>{formatUnix(job.finished_at)}</dd>
              </div>
            </dl>
          </Section>

          {job.blocked_reason ? <p className="notice notice--warn" role="status">{job.blocked_reason}</p> : null}
          <div className="notice">
            {job.experiment_id ? <a href={"#/experiments/"+job.experiment_id}>查看关联的单机 / 多机对照评测</a> : "普通作业：执行成功不等于通过独立正确性校验；没有关联单机基线时，不计算 η 和 S。"}
            {job.worker_ids ? <p>仅允许选中的 {job.worker_ids.length} 台设备计算：{job.worker_ids.join("、")}</p> : null}
          </div>
          <Section
            title="按设备分解"
            subtitle="各设备完成的任务数、耗时和成功任务占比"
          >
            {job.per_worker.length === 0 ? (
              <EmptyBox title="暂无设备处理记录" hint="设备成功处理任务后，这里会显示它的完成量和耗时。" />
            ) : (
              <div className="table-wrap" role="region" aria-label="设备处理记录" tabIndex={0}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>设备</th>
                      <th className="num">完成数</th>
                      <th className="num">平均耗时</th>
                      <th className="num">总耗时</th>
                      <th className="num">失败</th>
                      <th>完成占比</th>
                    </tr>
                  </thead>
                  <tbody>
                    {job.per_worker.map((row) => {
                      const share = clampPct(
                        row.share === undefined ? sharePct(row.completed, completedTotal) : row.share * 100,
                      );
                      return (
                        <tr key={row.worker_id}>
                          <td>
                            <div className="cell-title">{row.name ?? row.worker_id}</div>
                            <div className="cell-sub mono">{row.worker_id}</div>
                          </td>
                          <td className="num">{formatNumber(row.completed)}</td>
                          <td className="num">{formatDuration(row.mean_duration_s)}</td>
                          <td className="num">{formatDuration(row.total_duration_s)}</td>
                          <td className="num">{formatNumber(row.failed)}</td>
                          <td>
                            <div className="share-cell">
                              <div className="progress progress--sm" aria-hidden="true">
                                <span
                                  className="progress-fill progress-fill--succeeded"
                                  style={{ width: (share ?? 0) + "%" }}
                                />
                              </div>
                              <span className="share-text">{formatPct(share)}</span>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Section>

          <Section title="任务明细" subtitle={`共 ${tasks.length} 条，展开某一行可以查看输入与产物`}>
            {tasks.length === 0 ? (
              <EmptyBox title="暂无任务明细" hint="任务拆分后会显示每个子任务的状态与耗时。" />
            ) : (
              <>
                <div className="table-wrap" role="region" aria-label="子任务明细" tabIndex={0}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th className="num">#</th>
                        <th>子任务 ID</th>
                        <th>状态</th>
                        <th>失败原因</th>
                        <th>设备</th>
                        <th className="num">尝试</th>
                        <th className="num">耗时</th>
                        <th>输入与产物</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pageTasks.map((task) => {
                        const open = expanded.includes(task.task_id);
                        const panelId = "task-detail-" + task.task_id;
                        return (
                          <Fragment key={task.task_id}>
                            <tr className={open ? "task-row task-row--open" : "task-row"}>
                              <td className="num">{task.idx}</td>
                              <td className="mono" title={task.task_id}>
                                {task.task_id}
                              </td>
                              <td>
                                <StateBadge state={task.state} kind="task" />
                              </td>
                              <td className="task-error">{task.error ?? "—"}</td>
                              <td>{task.worker_name ?? task.worker ?? "—"}</td>
                              <td className="num">{task.attempt ?? "—"}</td>
                              <td className="num">{formatDuration(task.duration_s)}</td>
                              <td className="task-expand-cell">
                                <button
                                  type="button"
                                  className="btn btn--sm btn--ghost"
                                  aria-expanded={open}
                                  aria-controls={panelId}
                                  onClick={() => toggleTask(task.task_id)}
                                >
                                  {open ? "收起" : task.outputs.length > 0 ? "展开产物" : "展开"}
                                </button>
                              </td>
                            </tr>
                            {open ? (
                              <tr className="task-detail-row">
                                <td colSpan={8}>
                                  <div className="task-detail" id={panelId}>
                                    {task.summary ? (
                                      <p className="task-summary">{task.summary}</p>
                                    ) : null}
                                    {job.workload === "infer" ? <DetectionPreview api={api} task={task} /> : null}
                                    <div className="task-assets">
                                      <section className="task-asset-group">
                                        <h4>输入（{task.inputs.length}）</h4>
                                        <AssetList
                                          api={api}
                                          assets={task.inputs}
                                          emptyText="没有记录输入文件。"
                                          onDownload={(asset) => void handleAssetDownload(asset)}
                                        />
                                      </section>
                                      <section className="task-asset-group">
                                        <h4>输出（{task.outputs.length}）</h4>
                                        <AssetList
                                          api={api}
                                          assets={task.outputs}
                                          emptyText="还没有产物（任务未完成或没有输出）。"
                                          onDownload={(asset) => void handleAssetDownload(asset)}
                                        />
                                      </section>
                                    </div>
                                  </div>
                                </td>
                              </tr>
                            ) : null}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <Pagination
                  page={page}
                  pageSize={pageSize}
                  total={tasks.length}
                  onPageChange={setPage}
                  onPageSizeChange={(size) => {
                    setPageSize(size);
                    setPage(0);
                  }}
                />
              </>
            )}
          </Section>
        </>
      )}
    </div>
  );
}

function AssetList({
  api,
  assets,
  emptyText,
  onDownload,
}: {
  api: ApiClient;
  assets: BlobBrief[];
  emptyText: string;
  onDownload: (asset: BlobBrief) => void;
}) {
  if (assets.length === 0) return <p className="asset-empty">{emptyText}</p>;
  return (
    <ul className="asset-list">
      {assets.map((asset) => (
        <li key={asset.sha256 + ":" + asset.name} className="asset-item">
          {isImageAsset(asset) ? (
            <AuthedAssetImage api={api} asset={asset} />
          ) : (
            <span className="asset-thumb asset-thumb--file" aria-hidden="true">文件</span>
          )}
          <div className="asset-body">
            <div className="asset-name" title={asset.name}>{asset.name}</div>
            <div className="asset-meta">
              <span>{formatBytes(asset.size)}</span>
              <span className="mono" title={asset.sha256}>{asset.sha256.slice(0, 12)}</span>
            </div>
            {isJsonAsset(asset) ? <JsonSummary api={api} asset={asset} /> : null}
            <button type="button" className="btn btn--sm btn--ghost" onClick={() => onDownload(asset)}>
              下载
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}

/**
 * <img> 不能带 Authorization 头，所以先 fetch 成 Blob 再转 objectURL。
 * 默认不加载（只在展开的明细里渲染），卸载时 revoke；并发由模块级闸门限制。
 */
function AuthedAssetImage({ api, asset }: { api: ApiClient; asset: BlobBrief }) {
  const [state, setState] = useState<{ url: string | null; error: boolean }>({ url: null, error: false });

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    let release: (() => void) | null = null;
    let objectUrl: string | null = null;

    void (async () => {
      release = await acquireAssetSlot();
      if (cancelled) {
        release();
        return;
      }
      try {
        const blob = await api.fetchBlob(asset.url, controller.signal);
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ url: objectUrl, error: false });
      } catch (error) {
        if (!cancelled && !isAbortError(error)) setState({ url: null, error: true });
      } finally {
        release();
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (release) release();
    };
  }, [api, asset.url]);

  if (state.error) {
    return <span className="asset-thumb asset-thumb--error" role="img" aria-label={asset.name + " 缩略图加载失败"}>失败</span>;
  }
  if (!state.url) {
    return <span className="asset-thumb asset-thumb--loading" aria-hidden="true" />;
  }
  return (
    <img
      className="asset-thumb"
      src={state.url}
      width={64}
      height={64}
      loading="lazy"
      decoding="async"
      alt={asset.name + " 缩略图"}
    />
  );
}

/** 输出 JSON 的摘要：最多展示 8 个顶层字段；超过 256 KB 直接提示下载。 */
function JsonSummary({ api, asset }: { api: ApiClient; asset: BlobBrief }) {
  const [state, setState] = useState<{
    status: "loading" | "ready" | "error";
    entries: [string, string][];
    note: string | null;
  }>({ status: "loading", entries: [], note: null });

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    let release: (() => void) | null = null;

    void (async () => {
      release = await acquireAssetSlot();
      if (cancelled) {
        release();
        return;
      }
      try {
        const blob = await api.fetchBlob(asset.url, controller.signal);
        if (cancelled) return;
        if (blob.size > 256 * 1024) {
          setState({ status: "ready", entries: [], note: "JSON 超过 256 KB，请下载查看。" });
          return;
        }
        const text = await blob.text();
        if (cancelled) return;
        let parsed: unknown;
        try {
          parsed = JSON.parse(text);
        } catch {
          setState({ status: "ready", entries: [], note: "内容不是合法 JSON，请下载查看。" });
          return;
        }
        const record = asPlainRecord(parsed);
        if (!record) {
          setState({
            status: "ready",
            entries: [["值", Array.isArray(parsed) ? parsed.length + " 项" : previewJsonValue(parsed)]],
            note: null,
          });
          return;
        }
        const entries: [string, string][] = [];
        let hidden = 0;
        for (const [key, value] of Object.entries(record)) {
          if (entries.length >= 8) {
            hidden += 1;
            continue;
          }
          entries.push([key, previewJsonValue(value)]);
        }
        setState({
          status: "ready",
          entries,
          note: hidden > 0 ? "另有 " + hidden + " 个字段未展示。" : null,
        });
      } catch (error) {
        if (!cancelled && !isAbortError(error)) setState({ status: "error", entries: [], note: null });
      } finally {
        release();
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
      if (release) release();
    };
  }, [api, asset.url]);

  if (state.status === "loading") return <span className="json-summary json-summary--muted">正在读取 JSON…</span>;
  if (state.status === "error") return <span className="json-summary json-summary--err">JSON 读取失败</span>;
  return (
    <div className="json-summary">
      {state.entries.length > 0 ? (
        <dl className="json-summary-list">
          {state.entries.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {state.note ? <p className="json-summary-note">{state.note}</p> : null}
    </div>
  );
}
