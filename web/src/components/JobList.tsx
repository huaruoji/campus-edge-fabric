import { useState } from "react";
import type { JobState, JobSummary } from "../api";
import type { PollState } from "../hooks/usePolling";
import { formatNumber, formatUnix, jobProgress, JOB_STATE_LABELS } from "../format";
import { CopyId, EmptyBox, ErrorBox, LoadingLine, Section, StateBadge, UpdatedInfo } from "./ui";

interface Props {
  jobs: PollState<JobSummary[]>;
  onRefresh: () => void;
  /** 页面顶部已有全局错误横幅时，不再重复渲染同一条错误。 */
  hideError?: boolean;
}

const FILTER_OPTIONS: Array<{ value: JobState | "all"; label: string }> = [
  { value: "all", label: "全部状态" },
  { value: "queued", label: JOB_STATE_LABELS.queued },
  { value: "running", label: JOB_STATE_LABELS.running },
  { value: "succeeded", label: JOB_STATE_LABELS.succeeded },
  { value: "partial", label: JOB_STATE_LABELS.partial },
  { value: "failed", label: JOB_STATE_LABELS.failed },
  { value: "canceled", label: JOB_STATE_LABELS.canceled },
];

export function JobList({ jobs, onRefresh, hideError = false }: Props) {
  const [filter, setFilter] = useState<JobState | "all">("all");
  const all = jobs.data ?? [];
  const list = all
    .filter((job) => filter === "all" || job.state === filter)
    .sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));

  return (
    <Section
      actions={
        <>
          <UpdatedInfo updatedAt={jobs.updatedAt} />
          <select
            className="select select--sm"
            value={filter}
            onChange={(event) => setFilter(event.target.value as JobState | "all")}
            aria-label="按状态筛选任务"
          >
            {FILTER_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <button type="button" className="btn btn--sm btn--ghost" onClick={onRefresh}>
            刷新
          </button>
          <a className="btn btn--sm btn--primary" href="#/submit">
            打开算力助手
          </a>
        </>
      }
    >
      {jobs.error && !hideError ? (
        <ErrorBox error={jobs.error} onRetry={onRefresh} compact={Boolean(jobs.data)} />
      ) : null}

      {!jobs.data && jobs.loading ? <LoadingLine text="正在读取任务列表…" /> : null}
      {!jobs.data && !jobs.loading && !jobs.error ? (
        <EmptyBox title="暂无任务数据" hint="点击「刷新」重新获取任务列表。" />
      ) : null}
      {!jobs.data && jobs.error && hideError ? (
        <EmptyBox title="任务列表暂不可用" hint="请按上方提示检查连接，然后重新检测。" />
      ) : null}
      {jobs.data && all.length === 0 ? (
        <EmptyBox
          title="还没有提交过任务"
          hint="上传一批文件，用一句话描述要做的处理。确认后，任务会出现在这里。"
          action={<a className="btn btn--sm btn--primary" href="#/submit">提交第一个任务</a>}
        />
      ) : null}
      {all.length > 0 && list.length === 0 ? (
        <EmptyBox title="没有符合筛选条件的任务" hint="切换上方状态筛选查看其它任务。" />
      ) : null}

      {list.length > 0 ? (
        <div className="table-wrap" role="region" aria-label="任务列表" tabIndex={0}>
          <table className="data-table job-table">
            <thead>
              <tr>
                <th>任务</th>
                <th>工作负载</th>
                <th>状态</th>
                <th>进度</th>
                <th className="num">成功 / 失败</th>
                <th>创建时间</th>
                <th>完成时间</th>
              </tr>
            </thead>
            <tbody>
              {list.map((job) => {
                const progress = jobProgress(job);
                return (
                  <tr key={job.job_id}>
                    <td>
                      <a className="cell-title job-link" href={"#/jobs/" + encodeURIComponent(job.job_id)}>
                        {job.name || "未命名任务"}
                      </a>
                      <div className="cell-sub">
                        <CopyId value={job.job_id} />
                      </div>
                    </td>
                    <td>{job.workload ?? "—"}</td>
                    <td>
                      <StateBadge state={job.state} kind="job" />
                    </td>
                    <td>
                      <div className="progress-inline">
                        <div className="progress progress--sm" aria-hidden="true">
                          <span
                            className={"progress-fill progress-fill--" + job.state}
                            style={{ width: progress.pct + "%" }}
                          />
                        </div>
                        <span className="progress-text">
                          {progress.done}/{progress.total}
                        </span>
                      </div>
                    </td>
                    <td className="num">
                      {formatNumber(job.succeeded)} / {formatNumber(job.failed)}
                    </td>
                    <td className="nowrap">{formatUnix(job.created_at)}</td>
                    <td className="nowrap">{formatUnix(job.finished_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </Section>
  );
}
