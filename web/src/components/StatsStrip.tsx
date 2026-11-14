import type { HealthResponse, StatsResponse } from "../api";
import type { PollState } from "../hooks/usePolling";
import { formatDuration, formatNumber } from "../format";
import { ErrorBox, UpdatedInfo } from "./ui";

interface Props {
  stats: PollState<StatsResponse>;
  health: PollState<HealthResponse>;
  onRefresh: () => void;
}

export function StatsStrip({ stats, health, onRefresh }: Props) {
  const data = stats.data;
  const online = health.data?.workers_online ?? (data ? data.workers.length : null);
  const jobs = data?.jobs ?? {};

  return (
    <section className="stats-strip" aria-label="集群统计">
      <div className="stats-cards">
        <div className="stat-card">
          <div className="stat-label">任务吞吐</div>
          <div className="stat-value">
            {formatNumber(data?.tasks_per_min, 2)}
            <span className="stat-unit">tasks/min</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">单任务耗时 p50</div>
          <div className="stat-value">{formatDuration(data?.p50_duration_s)}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">在线设备</div>
          <div className="stat-value">
            {online === null ? "—" : formatNumber(online)}
            <span className="stat-unit">台</span>
          </div>
        </div>
        <div className="stat-card stat-card--wide">
          <div className="stat-label">任务总览</div>
          <div className="stat-chips">
            <span className="stat-chip">
              总计 <b>{formatNumber(jobs.total)}</b>
            </span>
            <span className="stat-chip stat-chip--info">
              排队 <b>{formatNumber(jobs.queued)}</b>
            </span>
            <span className="stat-chip stat-chip--info">
              运行 <b>{formatNumber(jobs.running)}</b>
            </span>
            <span className="stat-chip stat-chip--ok">
              成功 <b>{formatNumber(jobs.succeeded)}</b>
            </span>
            <span className="stat-chip stat-chip--err">
              失败 <b>{formatNumber(jobs.failed)}</b>
            </span>
          </div>
        </div>
      </div>

      {data?.bottleneck ? (
        <div className="bottleneck" role="status">
          <span className="bottleneck-label">瓶颈提示</span>
          <span>{data.bottleneck}</span>
        </div>
      ) : null}

      {stats.error ? <ErrorBox error={stats.error} onRetry={onRefresh} compact /> : null}

      <div className="strip-foot">
        <UpdatedInfo updatedAt={stats.updatedAt} />
        {health.data?.version ? <span className="strip-meta">版本 {health.data.version}</span> : null}
        {health.data?.uptime_s ? <span className="strip-meta">运行 {formatDuration(health.data.uptime_s)}</span> : null}
      </div>
    </section>
  );
}
