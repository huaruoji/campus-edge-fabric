import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import type { ConnectionSettings, HealthResponse } from "../api";
import { CefApiError, normalizeBaseUrl, toUserMessage } from "../api";
import type { PollState } from "../hooks/usePolling";

interface Props {
  settings: ConnectionSettings;
  health: PollState<HealthResponse>;
  onApply: (settings: ConnectionSettings) => void;
  probeError: CefApiError | null;
  authenticated: boolean;
  /** 侧栏底部面板是否展开（默认折叠，由 App 持有状态）。 */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 自增时把焦点交给 Token 输入框（空态里的「填写访问 Token」用）。 */
  focusTokenSignal: number;
}

type PillTone = "ok" | "warn" | "err" | "idle";

export function ConnectionBar({
  settings,
  health,
  onApply,
  probeError,
  authenticated,
  open,
  onOpenChange,
  focusTokenSignal,
}: Props) {
  const [draftUrl, setDraftUrl] = useState(settings.baseUrl);
  const [draftToken, setDraftToken] = useState(settings.token);
  const [showToken, setShowToken] = useState(false);

  useEffect(() => {
    setDraftUrl(settings.baseUrl);
    setDraftToken(settings.token);
  }, [settings.baseUrl, settings.token]);

  useEffect(() => {
    if (focusTokenSignal <= 0) return;
    document.getElementById("cef-access-token")?.focus();
  }, [focusTokenSignal]);

  const dirty =
    normalizeBaseUrl(draftUrl) !== normalizeBaseUrl(settings.baseUrl) || draftToken.trim() !== settings.token;

  // 连接状态归因：未配置 Token → 未配置；unauthorized → 认证失败；network → 连接失败；其它接口错误 → 接口异常
  let pillTone: PillTone = "idle";
  let pillText = "未配置 Token";
  let pillTitle = "向队长获取访问 Token";
  if (settings.token) {
    if (probeError?.kind === "unauthorized") {
      pillTone = "err";
      pillText = "认证失败";
      pillTitle = toUserMessage(probeError).title;
    } else if (probeError?.kind === "network") {
      pillTone = "err";
      pillText = "连接失败";
      pillTitle = toUserMessage(probeError).title;
    } else if (probeError) {
      pillTone = "warn";
      pillText = probeError.kind === "not_found" ? "接口不存在" : "接口异常";
      pillTitle = toUserMessage(probeError).title;
    } else if (health.data?.ok && authenticated) {
      pillTone = "ok";
      pillText = "已连接";
      pillTitle = "算力池连接正常";
    } else {
      pillTone = "idle";
      pillText = "连接中…";
      pillTitle = "正在检查算力池连接";
    }
  }

  const probeMessage = probeError ? toUserMessage(probeError) : null;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const baseUrl = normalizeBaseUrl(draftUrl) || normalizeBaseUrl(window.location.origin);
    onApply({ baseUrl, token: draftToken.trim() });
    onOpenChange(false);
  }

  function resetDraft() {
    setDraftUrl(settings.baseUrl);
    setDraftToken(settings.token);
  }

  return (
    <section className={"conn-panel" + (open ? " conn-panel--open" : "")} aria-label="连接设置">
      <button
        type="button"
        className="conn-toggle"
        onClick={() => onOpenChange(!open)}
        aria-expanded={open}
        aria-controls="cef-conn-form"
      >
        <span className={"dot dot--" + pillTone} aria-hidden="true" />
        <span className="conn-toggle-text">
          <span className="conn-status">{pillText}</span>
          <span className="conn-endpoint" title={settings.baseUrl || undefined}>
            {settings.baseUrl || "未设置地址"}
          </span>
        </span>
        <svg
          className={"conn-caret" + (open ? " conn-caret--open" : "")}
          width="12"
          height="12"
          viewBox="0 0 12 12"
          aria-hidden="true"
          focusable="false"
        >
          <path
            d="M2.5 4.5 6 8l3.5-3.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      <span className="visually-hidden" role="status" aria-live="polite">
        {pillText}，{pillTitle}
      </span>

      <form id="cef-conn-form" className="conn-form" onSubmit={handleSubmit} hidden={!open}>
        <label className="conn-field">
          <span className="conn-label">Coordinator 地址</span>
          <input
            className="conn-input"
            value={draftUrl}
            onChange={(event) => setDraftUrl(event.target.value)}
            placeholder="http://127.0.0.1:8080"
            spellCheck={false}
            autoComplete="off"
            inputMode="url"
          />
        </label>
        <div className="conn-field">
          <label className="conn-label" htmlFor="cef-access-token">
            访问 Token
          </label>
          <div className="token-wrap">
            <input
              id="cef-access-token"
              className="conn-input"
              type={showToken ? "text" : "password"}
              value={draftToken}
              onChange={(event) => setDraftToken(event.target.value)}
              placeholder="CEF_TOKEN"
              spellCheck={false}
              autoComplete="off"
            />
            <button
              type="button"
              className="token-toggle"
              onClick={() => setShowToken((value) => !value)}
              aria-label={showToken ? "隐藏 Token" : "显示 Token"}
              aria-controls="cef-access-token"
            >
              {showToken ? "隐藏" : "显示"}
            </button>
          </div>
        </div>

        {probeMessage ? (
          <p className="conn-error" role="alert">
            {probeMessage.title}。{probeMessage.detail}
          </p>
        ) : (
          <p className="conn-hint">地址与 Token 由队长提供；应用后会重新校验连接。</p>
        )}

        <div className="conn-actions">
          <button type="submit" className="btn btn--sm btn--primary">
            应用{dirty ? " *" : ""}
          </button>
          {dirty ? (
            <button type="button" className="btn btn--sm btn--ghost" onClick={resetDraft}>
              撤销
            </button>
          ) : null}
        </div>
      </form>
    </section>
  );
}
