import type { ReactNode } from "react";
import type { JobState, TaskState, WorkerState } from "../api";
import { toUserMessage } from "../api";
import {
  clampPct,
  formatClock,
  JOB_STATE_LABELS,
  TASK_STATE_LABELS,
  WORKER_STATE_LABELS,
} from "../format";

export type BadgeTone = "ok" | "warn" | "err" | "info" | "idle";
type AnyState = JobState | TaskState | WorkerState;

const STATE_TONES: Record<string, BadgeTone> = {
  queued: "idle",
  running: "info",
  leased: "info",
  succeeded: "ok",
  partial: "warn",
  failed: "err",
  canceled: "idle",
  online: "ok",
  draining: "warn",
  offline: "idle",
};

export function stateLabel(state: AnyState, kind: "job" | "task" | "worker"): string {
  if (kind === "job") return JOB_STATE_LABELS[state as JobState] ?? String(state);
  if (kind === "task") return TASK_STATE_LABELS[state as TaskState] ?? String(state);
  return WORKER_STATE_LABELS[state as WorkerState] ?? String(state);
}

export function StateBadge({ state, kind }: { state: AnyState; kind: "job" | "task" | "worker" }) {
  const tone = STATE_TONES[state] ?? "idle";
  return <span className={"badge badge--" + tone}>{stateLabel(state, kind)}</span>;
}

/** CPU / 内存 / GPU 百分比条。value 为 null 时显示空槽（表示无该指标）。 */
export function Bar({ value, tone }: { value: number | null | undefined; tone: "cpu" | "mem" | "gpu" }) {
  const pct = clampPct(value);
  return (
    <div
      className={"meter meter--" + tone}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct === null ? undefined : Math.round(pct)}
    >
      {pct !== null ? <span className={"meter-fill meter-fill--" + tone} style={{ width: pct + "%" }} /> : null}
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

export function LoadingLine({ text = "加载中…" }: { text?: string }) {
  return (
    <div className="loading-line">
      <Spinner />
      <span>{text}</span>
    </div>
  );
}

export function UpdatedInfo({ updatedAt }: { updatedAt: number | null }) {
  if (!updatedAt) return null;
  return <span className="updated-info">更新于 {formatClock(updatedAt)}</span>;
}

export function ErrorBox({
  error,
  onRetry,
  compact = false,
}: {
  error: unknown;
  onRetry?: () => void;
  compact?: boolean;
}) {
  const message = toUserMessage(error);
  return (
    <div className={"error-box" + (compact ? " error-box--compact" : "")} role="alert">
      <div className="error-head">
        <span className="error-icon" aria-hidden="true">!</span>
        <span className="error-title">{message.title}</span>
        {onRetry ? (
          <button type="button" className="btn btn--sm btn--ghost" onClick={onRetry}>
            重试
          </button>
        ) : null}
      </div>
      <p className="error-detail">{message.detail}</p>
    </div>
  );
}

export function EmptyBox({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {hint ? <div className="empty-hint">{hint}</div> : null}
    </div>
  );
}

export function Section({
  title,
  subtitle,
  actions,
  children,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="section">
      <div className="section-head">
        <div>
          <h2 className="section-title">{title}</h2>
          {subtitle ? <p className="section-sub">{subtitle}</p> : null}
        </div>
        {actions ? <div className="section-actions">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(Math.max(0, page), pages - 1);
  const start = total === 0 ? 0 : current * pageSize + 1;
  const end = Math.min(total, (current + 1) * pageSize);
  return (
    <div className="pagination">
      <span className="pagination-info">
        第 {start}-{end} 条 / 共 {total} 条
      </span>
      <label className="pagination-size">
        每页
        <select
          value={pageSize}
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
          className="select select--sm"
        >
          {[20, 50, 100].map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
        条
      </label>
      <div className="pagination-buttons">
        <button
          type="button"
          className="btn btn--sm btn--ghost"
          disabled={current <= 0}
          onClick={() => onPageChange(current - 1)}
        >
          上一页
        </button>
        <span className="pagination-page">
          {current + 1} / {pages}
        </span>
        <button
          type="button"
          className="btn btn--sm btn--ghost"
          disabled={current >= pages - 1}
          onClick={() => onPageChange(current + 1)}
        >
          下一页
        </button>
      </div>
    </div>
  );
}
