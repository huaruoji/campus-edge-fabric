import { useEffect, useState } from "react";
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

/** 状态徽标：软底 + 深色文字，始终带文字，不靠颜色单独表意。 */
export function StateBadge({ state, kind }: { state: AnyState; kind: "job" | "task" | "worker" }) {
  const tone = STATE_TONES[state] ?? "idle";
  return <span className={"badge badge--" + tone}>{stateLabel(state, kind)}</span>;
}

/** CPU / 内存 / GPU 百分比条。value 为 null 时显示空槽（表示无该指标）。 */
export function Bar({ value, tone, label }: { value: number | null | undefined; tone: "cpu" | "mem" | "gpu"; label: string }) {
  const pct = clampPct(value);
  return (
    <div
      className={"meter meter--" + tone}
      role={pct === null ? undefined : "meter"}
      aria-hidden={pct === null ? true : undefined}
      aria-label={pct === null ? undefined : label}
      aria-valuemin={pct === null ? undefined : 0}
      aria-valuemax={pct === null ? undefined : 100}
      aria-valuenow={pct === null ? undefined : Math.round(pct)}
    >
      {pct !== null ? <span className={"meter-fill meter-fill--" + tone} style={{ width: pct + "%" }} /> : null}
    </div>
  );
}

/** 加载占位：骨架条 + 说明文字（不用转圈）。 */
export function LoadingLine({ text = "加载中…" }: { text?: string }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <span className="loading-text">{text}</span>
      <span className="skeleton" aria-hidden="true" />
      <span className="skeleton skeleton--short" aria-hidden="true" />
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

export function EmptyBox({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {hint ? <div className="empty-hint">{hint}</div> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

export function Section({
  title,
  subtitle,
  actions,
  children,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const hasTitle = title !== undefined && title !== null;
  return (
    <section className="section">
      {hasTitle || actions ? (
        <div className={hasTitle ? "section-head" : "section-head section-head--toolbar"}>
          {hasTitle ? (
            <div className="section-titles">
              <h2 className="section-title">{title}</h2>
              {subtitle ? <p className="section-sub">{subtitle}</p> : null}
            </div>
          ) : null}
          {actions ? <div className="section-actions">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 非安全上下文或权限被拒：退回到选中复制
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

/** ID 文本：mono 12px + 点击复制（键盘可用，复制结果由 aria-live 播报）。 */
export function CopyId({ value, hint = "复制" }: { value: string; hint?: string }) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");

  useEffect(() => {
    if (state === "idle") return;
    const timer = window.setTimeout(() => setState("idle"), 1600);
    return () => window.clearTimeout(timer);
  }, [state]);

  return (
    <>
      <button
        type="button"
        className="copy-id"
        title={value}
        aria-label={"复制 ID " + value}
        onClick={() => {
          void copyText(value).then((ok) => setState(ok ? "ok" : "fail"));
        }}
      >
        <span className="copy-id-text">{value}</span>
        <span className="copy-id-hint" aria-hidden="true">
          {state === "ok" ? "已复制" : state === "fail" ? "复制失败" : hint}
        </span>
      </button>
      <span className="visually-hidden" role="status" aria-live="polite">
        {state === "ok" ? "ID 已复制到剪贴板" : state === "fail" ? "复制失败，请手动选择文本" : ""}
      </span>
    </>
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
    <div className="pagination" role="group" aria-label="分页">
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
