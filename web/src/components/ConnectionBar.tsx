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
}

export function ConnectionBar({ settings, health, onApply, probeError, authenticated }: Props) {
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
          <div className="conn-field">
            <label htmlFor="cef-access-token">访问 Token</label>
            <div className="token-wrap">
              <input
                id="cef-access-token"
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
          <div className="conn-actions">
            <button type="submit" className="btn btn--primary">
              应用{dirty ? " *" : ""}
            </button>
            <span className={"health-pill health-pill--" + pillTone} title={pillTitle} role="status">
              <span className={"dot dot--" + pillTone} aria-hidden="true" />
              {pillText}
            </span>
          </div>
        </form>
      </div>
    </header>
  );
}
