import { useEffect, useState } from "react";
import type { ApiClient } from "../api";
import { usePolling } from "../hooks/usePolling";
import {
  formatDuration,
  formatEta,
  formatNumber,
  formatPct,
  formatUnix,
  isTerminalJobState,
  jobProgress,
  sharePct,
} from "../format";
import {
  EmptyBox,
  ErrorBox,
  LoadingLine,
  Pagination,
  Section,
  StateBadge,
  UpdatedInfo,
} from "./ui";

interface Props {
  api: ApiClient;
  jobId: string;
  onBack: () => void;
}

/**
 * 任务详情：GET /api/v1/jobs/{id}，2 秒轮询直到离开页面。
 * 展示进度、ETA、per_worker 分解、任务明细（分页），并提供结果包下载。
 */
export function JobDetail({ api, jobId, onBack }: Props) {
  const poll = usePolling((signal) => api.getJob(jobId, signal), 2000, api.hasToken, [api, jobId]);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const [downloading, setDownloading] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const job = poll.data;
  const tasks = job?.tasks ?? [];
  const progress = job ? jobProgress(job) : { done: 0, total: 0, pct: 0 };
  const terminal = job ? isTerminalJobState(job.state) : false;

  useEffect(() => {
    const maxPage = Math.max(0, Math.ceil(tasks.length / pageSize) - 1);
    setPage((value) => Math.min(value, maxPage));
  }, [tasks.length, pageSize]);

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
      setNotice("结果包已开始下载：" + filename + "（含 manifest.json 与 outputs/）");
    } catch (cause) {
      setActionError(cause);
    } finally {
      setDownloading(false);
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
      setNotice("已发送取消请求（202），等待调度器收敛状态…");
      poll.refresh();
    } catch (cause) {
      setActionError(cause);
    } finally {
      setCanceling(false);
    }
  }

  const durationTotal = (job?.per_worker ?? []).reduce((sum, row) => sum + (row.total_duration_s ?? 0), 0);
  const pageTasks = tasks.slice(page * pageSize, page * pageSize + pageSize);

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
            title={terminal ? "下载 /api/v1/jobs/{id}/result 结果包" : "任务结束后才能下载结果包"}
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

      {poll.error ? <ErrorBox error={poll.error} onRetry={poll.refresh} compact={Boolean(job)} /> : null}
      {actionError ? <ErrorBox error={actionError} /> : null}
      {notice ? <div className="notice">{notice}</div> : null}

      {!job ? (
        poll.loading ? (
          <LoadingLine text="正在读取任务详情…" />
        ) : (
          <EmptyBox title="任务详情不可用" hint="请检查任务 ID 是否正确，或返回任务列表刷新后重试。" />
        )
      ) : (
        <>
          <Section
            title={job.name || "未命名任务"}
            subtitle={"工作负载 " + (job.workload ?? "—") + " · 创建于 " + formatUnix(job.created_at)}
            actions={<StateBadge state={job.state} kind="job" />}
          >
            <div className="progress-block">
              <div className="progress progress--lg">
                <span
                  className={"progress-fill progress-fill--" + job.state}
                  style={{ width: progress.pct + "%" }}
                />
              </div>
              <div className="progress-caption">
                <span>
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
                <dd>{formatEta(job.eta_s)}</dd>
              </div>
              <div className="kv">
                <dt>完成时间</dt>
                <dd>{formatUnix(job.finished_at)}</dd>
              </div>
            </dl>
          </Section>

          <Section
            title="按设备分解"
            subtitle="per_worker：每台设备实际完成的 Task 数与耗时占比"
          >
            {job.per_worker.length === 0 ? (
              <EmptyBox title="暂无设备处理记录" hint="任务尚未被 Worker 领取，或后端暂未返回 per_worker。" />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>设备</th>
                      <th className="num">完成数</th>
                      <th className="num">平均耗时</th>
                      <th className="num">总耗时</th>
                      <th className="num">失败</th>
                      <th>耗时占比</th>
                    </tr>
                  </thead>
                  <tbody>
                    {job.per_worker.map((row) => {
                      const share = sharePct(row.total_duration_s, durationTotal);
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
                              <div className="progress progress--sm">
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

          <Section title="任务明细" subtitle={"共 " + tasks.length + " 条 · 每 2 秒随详情刷新"}>
            {tasks.length === 0 ? (
              <EmptyBox title="暂无任务明细" hint="任务拆解完成后会显示每个 Task 的状态与耗时。" />
            ) : (
              <>
                <div className="table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th className="num">#</th>
                        <th>Task ID</th>
                        <th>状态</th>
                        <th>设备</th>
                        <th className="num">尝试</th>
                        <th className="num">耗时</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pageTasks.map((task) => (
                        <tr key={task.task_id}>
                          <td className="num">{task.idx}</td>
                          <td className="mono" title={task.task_id}>
                            {task.task_id}
                          </td>
                          <td>
                            <StateBadge state={task.state} kind="task" />
                          </td>
                          <td>{task.worker ?? "—"}</td>
                          <td className="num">{task.attempt ?? "—"}</td>
                          <td className="num">{formatDuration(task.duration_s)}</td>
                        </tr>
                      ))}
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
