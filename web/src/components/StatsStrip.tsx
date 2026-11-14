import type { HealthResponse, StatsResponse } from "../api";
import type { PollState } from "../hooks/usePolling";
import { formatDuration, formatNumber } from "../format";
import { ErrorBox, UpdatedInfo } from "./ui";

interface Props {
  stats: PollState<StatsResponse>;
  health: PollState<HealthResponse>;
  onRefresh: () => void;
  /** 页面顶部已有全局错误横幅时，不再重复渲染同一条错误。 */
  hideError?: boolean;
}

export function StatsStrip({ stats, health, onRefresh, hideError = false }: Props) {
  const data = stats.data;
  const online = health.data?.workers_online ?? data?.workers_online ?? (data ? data.workers.length : null);
  const jobs = data?.jobs ?? {};

  return (
    <section className="stats-strip" aria-label="集群统计">
      <div className="stats">
        <div className="stats-kpis">
          <div className="stat">
            <span className="stat-label">任务吞吐</span>
            <span className="stat-value">
              {formatNumber(data?.tasks_per_min, 2)}
              <span className="stat-unit">tasks/min</span>
            </span>
          </div>
          <div className="stat">
            <span className="stat-label">单任务耗时 p50</span>
            <span className="stat-value">{formatDuration(data?.p50_duration_s)}</span>
          </div>
          <div className="stat">
            <span className="stat-label">在线设备</span>
            <span className="stat-value">
              {online === null ? "—" : formatNumber(online)}
              <span className="stat-unit">台</span>
            </span>
          </div>
        </div>

        <div className="stats-overview">
          <h3 className="overview-title">作业与任务</h3>
          <dl className="overview-grid">
            <div>
              <dt>作业总数</dt>
              <dd>{formatNumber(jobs.total)}</dd>
            </div>
            <div>
              <dt>进行中作业</dt>
              <dd>{formatNumber(jobs.running)}</dd>
            </div>
            <div>
              <dt>排队任务</dt>
              <dd>{formatNumber(jobs.queued)}</dd>
            </div>
            <div>
              <dt>成功任务</dt>
              <dd>{formatNumber(jobs.succeeded)}</dd>
            </div>
            <div>
              <dt>失败任务</dt>
              <dd className={jobs.failed ? "text-err" : undefined}>{formatNumber(jobs.failed)}</dd>
            </div>
          </dl>
        </div>
      </div>

      {data?.bottleneck ? (
        <div className="bottleneck" role="status">
          <span className="bottleneck-label">瓶颈</span>
          <span>{data.bottleneck}</span>
        </div>
      ) : null}

      {stats.error && !hideError ? <ErrorBox error={stats.error} onRetry={onRefresh} compact /> : null}

      <div className="strip-foot">
        <UpdatedInfo updatedAt={stats.updatedAt} />
        {health.data?.version ? <span className="strip-meta">版本 {health.data.version}</span> : null}
        {health.data?.uptime_s ? <span className="strip-meta">已运行 {formatDuration(health.data.uptime_s)}</span> : null}
      </div>
    </section>
  );
}
