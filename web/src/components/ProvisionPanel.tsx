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
 * 后端契约（实测 2026-11-14，cef.huaruoji.com）：
 * - POST /api/v1/workers/{id}/provision {"recipe":"infer"|"blender","dry_run":bool}
 *   → 202，立刻返回一条 {id,state:"queued"}；真正的执行由设备在下一次心跳领取。
 * - GET 同路径 → {requests:[{id,recipe,dry_run,state,log,created_at,updated_at}]}（新→旧）。
 * - 设备没有 --allow-provision 时仍会接单，但会回报 failed + log「device not authorised」。
 * - 403 只可能来自反向代理/权限层；404 表示后端还没上线该接口，WorkerCard 隐藏入口。
 *
 * 前端无法判断某台设备是否被授权，所以按钮默认 disabled，只有用户明确勾选
 * 「设备所有者已授权」后才能点击；真正的授权校验由设备和后端兜底。
 */

const POLL_MS = 2500;
/** 超过这个时长就不再自动轮询，避免页面开着挂一堆请求。 */
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

/** 设备拒绝了这次请求：只有「接口存在但没授权」才算，其他错误不改变结论。 */
export function provisionSupportedFromError(error: unknown): boolean | null {
  if (error instanceof CefApiError) {
    if (error.kind === "not_found") return false;
    if (error.kind === "forbidden") return true;
  }
  return null;
}

/** 设备回报的「未授权」在 log 里，不是 HTTP 状态码。 */
function refusedByDevice(status: ProvisionStatus): boolean {
  return status.log.some((line) => /not authori[sz]ed/i.test(line));
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
  const disabledHintId = useId();
  const timerRef = useRef<number | undefined>(undefined);
  const startedAtRef = useRef<number>(0);
  const statusRef = useRef<ProvisionStatus | null>(initialStatus);
  statusRef.current = status;

  const stopPolling = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }
    setPolling(false);
  }, []);

  const pollOnce = useCallback(async () => {
    try {
      const list = await api.getProvision(worker.worker_id);
      const current = statusRef.current;
      const next = (current?.id ? list.find((item) => item.id === current.id) : undefined) ?? list[0] ?? null;
      setError(null);
      if (!next) {
        stopPolling();
        setNotice("设备还没有收到这条请求。确认设备在线、并且是最新版本的 worker。");
        return;
      }
      setStatus(next);
      if (isTerminal(next.state)) {
        stopPolling();
        onRefresh();
        if (next.state === "succeeded") {
          setNotice(next.dry_run ? "预演完成，这台设备可以跑这个配置。" : "设备已完成环境配置。");
        } else if (refusedByDevice(next)) {
          setNotice("设备拒绝执行：没有授权远程配置。");
        } else {
          setNotice("设备报告配置失败，请查看下面的日志。");
        }
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
      return;
    }
  }, [api, onRefresh, stopPolling, worker.worker_id]);

  const startPolling = useCallback(() => {
    stopPolling();
    startedAtRef.current = Date.now();
    setPolling(true);
    const tick = (): void => {
      timerRef.current = window.setTimeout(() => {
        void pollOnce().then(() => {
          timerRef.current = window.setTimeout(tick, POLL_MS);
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
        setNotice(next.state === "succeeded" ? "设备已完成环境配置。" : "设备报告配置失败，请查看下面的日志。");
      } else {
        setNotice(runDry ? "已请求预演，设备处理中…" : "已请求，设备处理中…");
        startPolling();
      }
    } catch (cause) {
      setError(cause);
    } finally {
      setRequesting(false);
    }
  }

  const canRequest = online && authorized && !requesting;
  const disabledReason = !online
    ? "设备离线，无法请求远程配置。"
    : !authorized
      ? "需要设备所有者预先授权。"
      : "";
  const refused = status ? refusedByDevice(status) : false;

  return (
    <div className="provision-panel">
      <div className="provision-head">
        <span className="provision-title">配置环境</span>
        <span className="field-hint">Coordinator 只转发 recipe 名字，命令由设备本机的固定脚本执行。</span>
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
          先预演（dry_run，不安装任何东西）
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
          aria-describedby={disabledReason ? disabledHintId : undefined}
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
        <p className="provision-hint" id={disabledHintId}>
          {disabledReason}
          {!authorized ? " 授权的做法：设备所有者在目标设备上用 --allow-provision infer,blender 重启 worker。" : ""}
        </p>
      ) : null}

      {error ? (
        <div className="provision-error">
          <ErrorBox error={error} compact />
        </div>
      ) : null}
      {notice ? <div className="notice" role="status" aria-live="polite">{notice}</div> : null}

      {refused ? (
        <p className="provision-hint provision-hint--err">
          这台设备没有授权远程配置。请在目标设备上确认 worker 启动时带了 --allow-provision infer,blender，然后重试。
        </p>
      ) : null}

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
          {status.log.length > 0 ? (
            <pre className="provision-log">{status.log.join("\n")}</pre>
          ) : (
            <p className="provision-hint">
              {status.state === "queued" ? "还没有设备领取这条请求；设备下一次心跳会接走它。" : "还没有日志输出。"}
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
