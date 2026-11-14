import { useCallback, useEffect, useId, useRef, useState } from "react";
import type {
  ApiClient,
  ProvisionRecipe,
  ProvisionState,
  ProvisionStatus,
  Worker,
} from "../api";
import { CefApiError } from "../api";
import { formatClock } from "../format";
import { ErrorBox } from "./ui";
import "../chat.css";

/**
 * 设备「配置环境」入口。
 *
 * 后端契约（POST/GET /api/v1/workers/{id}/provision）：
 * - POST {"recipe":"infer"|"blender","dry_run":true|false} → 202 表示已入队；
 * - 403 = 设备没有授权远程配置（设备所有者要用 --allow-provision 重启 worker）；
 * - 404 = 后端还没上线该接口，整个入口隐藏（由 WorkerCard 处理）；
 * - GET 返回 {state: queued|running|succeeded|failed, log, ...}，界面轮询到终态为止。
 *
 * 前端无法判断某台设备是否被授权，所以按钮默认 disabled，只有用户明确勾选
 * 「设备所有者已授权」后才可点击；真正的授权校验仍然由后端 403 兜底。
 */

const POLL_MS = 2500;
/** 超过这个时长就不再自动轮询，避免页面开着挂后台请求。 */
const POLL_BUDGET_MS = 10 * 60 * 1000;

const STATE_LABELS: Record<ProvisionState, string> = {
  queued: "排队中",
  running: "配置中",
  succeeded: "已完成",
  failed: "失败",
};

const RECIPE_LABELS: Record<ProvisionRecipe, string> = {
  infer: "infer（目标检测 / onnxruntime）",
  blender: "blender（渲染场景）",
};

function isTerminal(state: ProvisionState): boolean {
  return state === "succeeded" || state === "failed";
}

/** 设备是否授权远程配置：仅 403 说明「接口存在但没授权」，其他错误不改变结论。 */
export function provisionSupportedFromError(error: unknown): boolean | null {
  if (error instanceof CefApiError) {
    if (error.kind === "not_found") return false;
    if (error.kind === "forbidden") return true;
  }
  return null;
}

function describeError(error: unknown): string {
  if (error instanceof CefApiError) {
    if (error.status === 403) {
      return "这台设备没有授权远程配置。请在目标设备上用 --allow-provision infer,blender 重启 worker，再回来重试。";
    }
    if (error.status === 404) {
      return "后端还没提供配置环境接口（404），入口已隐藏。";
    }
    if (error.status === 409) {
      return "该设备已有配置任务在跑，等它结束再试。";
    }
  }
  return "";
}

export function ProvisionPanel({
  api,
  worker,
  initialStatus,
  onRefresh,
}: {
  api: ApiClient;
  worker: Worker;
  initialStatus: ProvisionStatus | null;
  onRefresh: () => void;
}) {
  const online = worker.state !== "offline";
  const [recipe, setRecipe] = useState<ProvisionRecipe>("infer");
  const [dryRun, setDryRun] = useState(true);
  const [authorized, setAuthorized] = useState(false);
  const [status, setStatus] = useState<ProvisionStatus | null>(initialStatus);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);

  const recipeId = useId();
  const dryRunId = useId();
  const authorizedId = useId();
  const timerRef = useRef<number | undefined>(undefined);
  const startedAtRef = useRef<number>(0);

  const stopPolling = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }
    setPolling(false);
  }, []);

  const pollOnce = useCallback(async () => {
    try {
      const next = await api.getProvision(worker.worker_id);
      setStatus(next);
      setError(null);
      if (isTerminal(next.state)) {
        stopPolling();
        onRefresh();
        setNotice(next.state === "succeeded" ? "设备已完成环境配置。" : "设备配置失败，请查看下面的日志。");
        return;
      }
    } catch (cause) {
      stopPolling();
      setError(cause);
      return;
    }
    if (Date.now() - startedAtRef.current > POLL_BUDGET_MS) {
      stopPolling();
      setNotice("已停止自动刷新。稍后点「刷新状态」继续查看。");
    }
  }, [api, onRefresh, stopPolling, worker.worker_id]);

  const startPolling = useCallback(() => {
    stopPolling();
    startedAtRef.current = Date.now();
    setPolling(true);
    const tick = (): void => {
      timerRef.current = window.setTimeout(() => {
        void pollOnce().then(() => {
          if (timerRef.current !== undefined) {
            // pollOnce 里已经 stopPolling 时不会留下 timer。
            tick();
          }
        });
      }, POLL_MS);
    };
    tick();
  }, [pollOnce, stopPolling]);

  useEffect(() => () => {
    if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
  }, []);

  async function request(runDry: boolean): Promise<void> {
    if (requesting) return;
    setRequesting(true);
    setError(null);
    setNotice(null);
    try {
      const next = await api.provisionWorker(worker.worker_id, { recipe, dry_run: runDry });
      setStatus(next);
      if (isTerminal(next.state)) {
        setNotice(
          next.state === "succeeded"
            ? runDry
              ? "预演已完成，设备环境可以支持这个配置。"
              : "设备已完成环境配置。"
            : "设备报告配置失败，请查看下面的日志。"
        );
        if (next.state === "succeeded" && !runDry) onRefresh();
      } else {
        setNotice(runDry ? "已请求预演，设备处理中…" : "已请求，设备处理中…");
        startPolling();
      }
    } catch (cause) {
      setStatus(null);
      setError(cause);
    } finally {
      setRequesting(false);
    }
  }

  const canRequest = online && authorized && !requesting;
  const disabledReason = !online
    ? "设备离线，无法请求远程配置。"
    : !authorized
      ? "需要设备所有者预先授权：勾选下面的确认项才能请求。"
      : "";

  return (
    <div className="provision-panel">
      <div className="provision-head">
        <span className="provision-title">配置环境</span>
        <span className="field-hint">由设备本机执行 provision recipe，Coordinator 只转发请求。</span>
      </div>

      <div className="provision-controls">
        <label htmlFor={recipeId} className="field-label">recipe</label>
        <select
          id={recipeId}
          className="select select--sm"
          value={recipe}
          onChange={(event) => setRecipe(event.target.value as ProvisionRecipe)}
          disabled={requesting}
        >
          {(Object.keys(RECIPE_LABELS) as ProvisionRecipe[]).map((value) => (
            <option key={value} value={value}>{RECIPE_LABELS[value]}</option>
          ))}
        </select>
        <label className="provision-check" htmlFor={dryRunId}>
          <input
            id={dryRunId}
            type="checkbox"
            checked={dryRun}
            onChange={(event) => setDryRun(event.target.checked)}
            disabled={requesting}
          />
          先预演（dry_run，不改动设备）
        </label>
      </div>

      <label className="provision-check" htmlFor={authorizedId}>
        <input
          id={authorizedId}
          type="checkbox"
          checked={authorized}
          onChange={(event) => setAuthorized(event.target.checked)}
          disabled={!online}
        />
        我已确认设备所有者授权远程配置
      </label>

      <div className="provision-actions">
        <button
          type="button"
          className="btn btn--sm btn--primary"
          onClick={() => void request(dryRun)}
          disabled={!canRequest}
          title={disabledReason || (dryRun ? "在设备上预演一次配置，不落盘" : "在设备上真正执行配置")}
          aria-describedby={disabledReason ? "provision-disabled-" + worker.worker_id : undefined}
        >
          {requesting ? "请求中…" : dryRun ? "预演配置" : "执行配置"}
        </button>
        <button
          type="button"
          className="btn btn--sm btn--ghost"
          onClick={() => {
            setError(null);
            setNotice("正在读取配置状态…");
            startedAtRef.current = Date.now();
            void pollOnce();
          }}
          disabled={requesting || !online}
        >
          刷新状态
        </button>
        {polling ? <span className="field-hint" role="status">每 {POLL_MS / 1000} 秒自动刷新</span> : null}
      </div>

      {disabledReason ? (
        <p className="provision-hint" id={"provision-disabled-" + worker.worker_id}>
          {disabledReason}
          {!authorized ? " 授权的含义：设备所有者用 --allow-provision " + recipe + " 重启 worker。" : ""}
        </p>
      ) : null}

      {error ? (
        <div className="provision-error" role="alert">
          <ErrorBox error={error} compact />
          {describeError(error) ? <p className="provision-hint">{describeError(error)}</p> : null}
        </div>
      ) : null}
      {notice ? <div className="notice" role="status" aria-live="polite">{notice}</div> : null}

      {status ? (
        <div className="provision-status">
          <div className="provision-status-head">
            <span className={"provision-state provision-state--" + status.state}>
              {STATE_LABELS[status.state]}
            </span>
            <span className="field-hint">
              recipe {status.recipe || recipe}
              {status.dry_run ? "（预演）" : ""}
              {status.updated_at ? " · 更新于 " + formatClock(status.updated_at * 1000) : ""}
            </span>
          </div>
          {status.error ? <p className="provision-hint provision-hint--err">{status.error}</p> : null}
          {status.log.length > 0 ? (
            <pre className="provision-log">{status.log.join("\n")}</pre>
          ) : (
            <p className="provision-hint">还没有日志输出。</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
