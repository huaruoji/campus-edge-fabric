import { useState } from "react";
import type { ApiClient, Worker, WorkerState } from "../api";
import type { PollState } from "../hooks/usePolling";
import { formatDuration, formatNumber, formatPct, formatRelativeSeconds } from "../format";
import { Bar, EmptyBox, ErrorBox, LoadingLine, Section, StateBadge, UpdatedInfo } from "./ui";

interface Props {
  workers: PollState<Worker[]>;
  api: ApiClient;
  onRefresh: () => void;
  /** 页面顶部已有全局错误横幅时，不再重复渲染同一条错误。 */
  hideError?: boolean;
}

export function WorkerPanel({ workers, api, onRefresh, hideError = false }: Props) {
  const stateOrder: Record<WorkerState, number> = { online: 0, draining: 1, offline: 2 };
  const list = [...(workers.data ?? [])].sort((a, b) => stateOrder[a.state] - stateOrder[b.state]);
  const onlineCount = list.filter((worker) => worker.state === "online").length;
  return (
    <Section
      title="设备面板"
      subtitle={workers.data ? `${onlineCount} 台在线 · ${list.length} 台已登记` : undefined}
      actions={
        <>
          <UpdatedInfo updatedAt={workers.updatedAt} />
          <button type="button" className="btn btn--sm btn--ghost" onClick={onRefresh}>
            刷新
          </button>
        </>
      }
    >
      {workers.error && !hideError ? (
        <ErrorBox error={workers.error} onRetry={onRefresh} compact={Boolean(workers.data)} />
      ) : null}

      {!workers.data && workers.loading ? <LoadingLine text="正在读取设备列表…" /> : null}
      {!workers.data && !workers.loading && !workers.error ? (
        <EmptyBox title="暂无设备数据" hint="点击「刷新」重新获取设备列表。" />
      ) : null}
      {!workers.data && workers.error && hideError ? (
        <EmptyBox title="设备数据暂不可用" hint="请按上方提示检查连接，然后重新检测。" />
      ) : null}
      {workers.data && list.length === 0 ? (
        <EmptyBox
          title="还没有设备加入"
          hint="在队员的电脑上用上方的地址和 Token 启动 Worker。设备连接后会自动出现在这里。"
        />
      ) : null}

      {list.length > 0 ? (
        <>
          {onlineCount === 0 ? <p className="notice notice--warn">当前没有在线设备。启动或重连一台 Worker 后，排队任务就可以继续处理。</p> : null}
          <div className="worker-grid">
            {list.map((worker) => (
              <WorkerCard key={worker.worker_id} worker={worker} api={api} onRemoved={onRefresh} />
            ))}
          </div>
        </>
      ) : null}
    </Section>
  );
}

function WorkerCard({
  worker,
  api,
  onRemoved,
}: {
  worker: Worker;
  api: ApiClient;
  onRemoved: () => void;
}) {
  const offline = worker.state === "offline";
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<unknown>(null);

  async function forget() {
    if (removing || !window.confirm(`移除离线设备「${worker.name}」的记录？`)) return;
    setRemoveError(null);
    setRemoving(true);
    try {
      await api.forgetWorker(worker.worker_id);
      onRemoved();
    } catch (error) {
      setRemoveError(error);
    } finally {
      setRemoving(false);
    }
  }
  return (
    <article className={"worker-card" + (offline ? " worker-card--offline" : "")}>
      <div className="worker-head">
        <span className={"dot dot--" + (offline ? "idle" : worker.state === "draining" ? "warn" : "ok")} aria-hidden="true" />
        <h3 className="worker-name" title={worker.worker_id}>
          {worker.name}
        </h3>
        <StateBadge state={worker.state} kind="worker" />
        {offline ? (
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={() => void forget()}
            disabled={removing}
            aria-label={`移除离线设备 ${worker.name}`}
          >
            {removing ? "移除中…" : "移除"}
          </button>
        ) : null}
      </div>
      {removeError ? <ErrorBox error={removeError} compact /> : null}

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
      <Bar value={value} tone={tone} label={label} />
      <span className="meter-value">{formatPct(value)}</span>
    </div>
  );
}
