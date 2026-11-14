import type { Worker } from "../api";
import type { PollState } from "../hooks/usePolling";
import { formatDuration, formatNumber, formatPct, formatRelativeSeconds } from "../format";
import { Bar, EmptyBox, ErrorBox, LoadingLine, Section, StateBadge, UpdatedInfo } from "./ui";

interface Props {
  workers: PollState<Worker[]>;
  onRefresh: () => void;
}

export function WorkerPanel({ workers, onRefresh }: Props) {
  const list = workers.data ?? [];
  return (
    <Section
      title={"设备面板" + (workers.data ? " · " + list.length + " 台" : "")}
      subtitle="每 3 秒轮询 GET /api/v1/workers；离线设备灰显。"
      actions={
        <>
          <UpdatedInfo updatedAt={workers.updatedAt} />
          <button type="button" className="btn btn--sm btn--ghost" onClick={onRefresh}>
            刷新
          </button>
        </>
      }
    >
      {workers.error ? (
        <ErrorBox error={workers.error} onRetry={onRefresh} compact={Boolean(workers.data)} />
      ) : null}

      {!workers.data && workers.loading ? <LoadingLine text="正在读取设备列表…" /> : null}
      {!workers.data && !workers.loading && !workers.error ? (
        <EmptyBox title="暂无设备数据" hint="Worker 启动并注册后会出现在这里。" />
      ) : null}
      {workers.data && list.length === 0 ? (
        <EmptyBox
          title="当前没有 Worker 注册"
          hint="启动 worker agent（POST /api/v1/workers/register）后自动出现，心跳间隔约 5 秒。"
        />
      ) : null}

      {list.length > 0 ? (
        <div className="worker-grid">
          {list.map((worker) => (
            <WorkerCard key={worker.worker_id} worker={worker} />
          ))}
        </div>
      ) : null}
    </Section>
  );
}

function WorkerCard({ worker }: { worker: Worker }) {
  const offline = worker.state === "offline";
  return (
    <article className={"worker-card" + (offline ? " worker-card--offline" : "")}>
      <div className="worker-head">
        <span className={"dot dot--" + (offline ? "idle" : "ok")} aria-hidden="true" />
        <h3 className="worker-name" title={worker.worker_id}>
          {worker.name}
        </h3>
        <StateBadge state={worker.state} kind="worker" />
      </div>

      <div className="worker-meta">
        <span>{worker.os ?? "未知系统"}</span>
        <span>{worker.cpu_count ? worker.cpu_count + " 核" : "— 核"}</span>
        <span>
          槽位 {worker.running ?? 0}/{worker.slots ?? "—"}
        </span>
      </div>

      {worker.tags.length > 0 ? (
        <div className="worker-tags">
          {worker.tags.map((tag) => (
            <span key={tag} className="tag">
              {tag}
            </span>
          ))}
        </div>
      ) : null}

      <div className="meters">
        <MeterRow label="CPU" value={worker.cpu_pct} tone="cpu" />
        <MeterRow label="内存" value={worker.mem_pct} tone="mem" />
        <MeterRow label="GPU" value={worker.gpu_pct} tone="gpu" />
      </div>

      <dl className="worker-stats">
        <div>
          <dt>已完成</dt>
          <dd>{formatNumber(worker.completed_total)}</dd>
        </div>
        <div>
          <dt>平均耗时</dt>
          <dd>{formatDuration(worker.mean_duration_s)}</dd>
        </div>
        <div>
          <dt>吞吐</dt>
          <dd>{formatNumber(worker.throughput_per_min, 2)} /min</dd>
        </div>
        <div>
          <dt>最后心跳</dt>
          <dd>{formatRelativeSeconds(worker.last_seen_s_ago)}</dd>
        </div>
      </dl>
    </article>
  );
}

function MeterRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | null;
  tone: "cpu" | "mem" | "gpu";
}) {
  return (
    <div className="meter-row">
      <span className="meter-label">{label}</span>
      <Bar value={value} tone={tone} />
      <span className="meter-value">{formatPct(value)}</span>
    </div>
  );
}
