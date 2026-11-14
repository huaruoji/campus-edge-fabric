import { useState } from "react";
import type { JobState, JobSummary } from "../api";
import type { PollState } from "../hooks/usePolling";
import {
  formatEta,
  formatNumber,
  formatUnix,
  jobProgress,
  JOB_STATE_LABELS,
} from "../format";
import { EmptyBox, ErrorBox, LoadingLine, Section, StateBadge, UpdatedInfo } from "./ui";

interface Props {
  jobs: PollState<JobSummary[]>;
  onOpen: (jobId: string) => void;
  onRefresh: () => void;
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

export function JobList({ jobs, onOpen, onRefresh }: Props) {
  const [filter, setFilter] = useState<JobState | "all">("all");
  const all = jobs.data ?? [];
  const list = all
    .filter((job) => filter === "all" || job.state === filter)
    .slice()
    .sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));

  return (
    <Section
      title="任务列表"
      subtitle="GET /api/v1/jobs?limit=50 · 每 5 秒刷新 · 点击行查看详情"
      actions={
        <>
          <UpdatedInfo updatedAt={jobs.updatedAt} />
          <select
            className="select select--sm"
            value={filter}
            onChange={(event) => setFilter(event.target.value as JobState | "all")}
            aria-label="按状态筛选"
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
            提交任务
          </a>
        </>
      }
    >
      {jobs.error ? <ErrorBox error={jobs.error} onRetry={onRefresh} compact={Boolean(jobs.data)} /> : null}

      {!jobs.data && jobs.loading ? <LoadingLine text="正在读取任务列表…" /> : null}
      {!jobs.data && !jobs.loading && !jobs.error ? (
        <EmptyBox title="暂无任务数据" hint="Coordinator 返回的空列表，或任务接口尚未就绪。" />
      ) : null}
      {jobs.data && all.length === 0 ? (
        <EmptyBox
          title="还没有提交过任务"
          hint={
            <>
              去 <a href="#/submit">提交任务</a> 页选择 workload 并上传文件，创建后会出现在这里。
            </>
          }
        />
      ) : null}
      {all.length > 0 && list.length === 0 ? (
        <EmptyBox title="没有符合筛选条件的任务" hint="切换上方状态筛选查看其它任务。" />
      ) : null}

      {list.length > 0 ? (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>任务</th>
                <th>工作负载</th>
                <th>状态</th>
                <th>进度</th>
                <th className="num">成功 / 失败</th>
                <th>创建时间</th>
                <th className="num">预计剩余</th>
                <th aria-label="操作" />
              </tr>
            </thead>
            <tbody>
              {list.map((job) => {
                const progress = jobProgress(job);
                return (
                  <tr
                    key={job.job_id}
                    className="row-clickable"
                    tabIndex={0}
                    onClick={() => onOpen(job.job_id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onOpen(job.job_id);
                      }
                    }}
                  >
                    <td>
                      <div className="cell-title">{job.name || "未命名任务"}</div>
                      <div className="cell-sub mono">{job.job_id}</div>
                    </td>
                    <td>{job.workload ?? "—"}</td>
                    <td>
                      <StateBadge state={job.state} kind="job" />
                    </td>
                    <td>
                      <div className="progress-inline">
                        <div className="progress progress--sm">
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
                    <td className="num nowrap">{formatEta(job.eta_s)}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn--sm btn--ghost"
                        onClick={(event) => {
                          event.stopPropagation();
                          onOpen(job.job_id);
                        }}
                      >
                        详情
                      </button>
                    </td>
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
