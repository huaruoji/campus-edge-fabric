import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import type { ConnectionSettings, HealthResponse } from "../api";
import { CefApiError, normalizeBaseUrl, toUserMessage } from "../api";
import type { PollState } from "../hooks/usePolling";

interface Props {
  settings: ConnectionSettings;
  health: PollState<HealthResponse>;
  onApply: (settings: ConnectionSettings) => void;
  /** 需要鉴权接口的最近一次错误：/health 免鉴权，Token 错误只能从这里看出来。 */
  probeError?: unknown;
}

export function ConnectionBar({ settings, health, onApply, probeError }: Props) {
  const [draftUrl, setDraftUrl] = useState(settings.baseUrl);
  const [draftToken, setDraftToken] = useState(settings.token);
  const [showToken, setShowToken] = useState(false);

  useEffect(() => {
    setDraftUrl(settings.baseUrl);
    setDraftToken(settings.token);
  }, [settings.baseUrl, settings.token]);

  const dirty =
    normalizeBaseUrl(draftUrl) !== normalizeBaseUrl(settings.baseUrl) || draftToken.trim() !== settings.token;

  let pillTone = "idle";
  let pillText = "未配置 Token";
  let pillTitle = "在下方填写与后端 CEF_TOKEN 一致的 Token";
  if (settings.token) {
    const unauthorized = probeError instanceof CefApiError && probeError.kind === "unauthorized";
    const network = health.error instanceof CefApiError && health.error.kind === "network";
    if (unauthorized) {
      pillTone = "err";
      pillText = "认证失败";
      pillTitle = "Token 无效：鉴权接口返回 401";
    } else if (network) {
      pillTone = "err";
      pillText = "连接失败";
      pillTitle = toUserMessage(health.error).title;
    } else if (health.error) {
      pillTone = "warn";
      pillText = "接口异常";
      pillTitle = toUserMessage(health.error).title;
    } else if (health.data) {
      pillTone = "ok";
      pillText = "已连接";
      pillTitle = "Coordinator 健康检查正常";
    } else {
      pillTone = "idle";
      pillText = "连接中…";
      pillTitle = "正在请求 /api/v1/health";
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const baseUrl = normalizeBaseUrl(draftUrl) || normalizeBaseUrl(window.location.origin);
    onApply({ baseUrl, token: draftToken.trim() });
  }

  return (
    <header className="app-header">
      <div className="header-inner">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">CEF</div>
          <div className="brand-text">
            <h1>Campus Edge Fabric</h1>
            <p>校园边缘算力 · 调度仪表盘</p>
          </div>
        </div>

        <form className="conn-bar" onSubmit={handleSubmit}>
          <label className="conn-field conn-field--url">
            <span>Coordinator 地址</span>
            <input
              value={draftUrl}
              onChange={(event) => setDraftUrl(event.target.value)}
              placeholder="http://127.0.0.1:8080"
              spellCheck={false}
              autoComplete="off"
              inputMode="url"
            />
          </label>
          <label className="conn-field">
            <span>访问 Token</span>
            <div className="token-wrap">
              <input
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
              >
                {showToken ? "隐藏" : "显示"}
              </button>
            </div>
          </label>
          <div className="conn-actions">
            <button type="submit" className="btn btn--primary">
              应用{dirty ? " *" : ""}
            </button>
            <span className={"health-pill health-pill--" + pillTone} title={pillTitle}>
              <span className={"dot dot--" + pillTone} aria-hidden="true" />
              {pillText}
            </span>
          </div>
          <p className="conn-hint">Token 仅保存在本机 localStorage；所有请求自动携带 Authorization: Bearer 头。</p>
        </form>
      </div>
    </header>
  );
}
