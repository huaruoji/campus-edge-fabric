import { useEffect, useMemo, useRef, useState } from "react";
import type { ConnectionSettings } from "./api";
import { CefApiError, createApiClient, loadSettings, saveSettings, toUserMessage } from "./api";
import { usePolling } from "./hooks/usePolling";
import { ConnectionBar } from "./components/ConnectionBar";
import { StatsStrip } from "./components/StatsStrip";
import { WorkerPanel } from "./components/WorkerPanel";
import { JobChat } from "./components/JobChat";
import { JobList } from "./components/JobList";
import { JobDetail } from "./components/JobDetail";
import { EmptyBox } from "./components/ui";

type Route = { name: "devices" } | { name: "submit" } | { name: "jobs" } | { name: "job"; jobId: string };

type NavName = "devices" | "submit" | "jobs";

const ROUTE_TITLES: Record<Route["name"], string> = {
  // 各页内部的区块标题由 WorkerPanel / JobChat / JobDetail 提供，
  // 页头标题刻意不与这些区块标题重复（设备面板 / 算力助手 / 任务列表）。
  devices: "集群概览",
  submit: "提交任务",
  jobs: "任务列表",
  job: "任务详情",
};

const ROUTE_DESCRIPTIONS: Record<Route["name"], string> = {
  devices: "查看每台边缘节点的在线状态、负载与吞吐；离线设备可以移除记录。",
  submit: "用一句话描述要做的处理，助手会给出工作负载与参数，确认后提交。",
  jobs: "按创建时间倒序显示最近 50 个作业，点击任务名查看明细。",
  job: "单个作业的进度、按设备分解和结果包下载。",
};

const NAV_ITEMS: Array<{ hash: string; name: NavName; label: string }> = [
  { hash: "#/devices", name: "devices", label: "设备面板" },
  { hash: "#/submit", name: "submit", label: "算力助手" },
  { hash: "#/jobs", name: "jobs", label: "任务列表" },
];

function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, "");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "submit") return { name: "submit" };
  if (parts[0] === "jobs" && parts[1]) {
    try {
      return { name: "job", jobId: decodeURIComponent(parts[1]) };
    } catch {
      return { name: "jobs" };
    }
  }
  if (parts[0] === "jobs") return { name: "jobs" };
  return { name: "devices" };
}

export default function App() {
  const [settings, setSettings] = useState<ConnectionSettings>(() => loadSettings());
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  const [connectionVersion, setConnectionVersion] = useState(0);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [tokenFocusSignal, setTokenFocusSignal] = useState(0);
  const mainRef = useRef<HTMLElement>(null);
  const focusOnNavigation = useRef(false);

  useEffect(() => {
    function onHashChange() {
      focusOnNavigation.current = true;
      setRoute(parseRoute(window.location.hash));
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    document.title = ROUTE_TITLES[route.name] + " · Campus Edge Fabric";
    if (focusOnNavigation.current) {
      focusOnNavigation.current = false;
      mainRef.current?.focus();
    }
  }, [route]);

  const api = useMemo(
    () => createApiClient(settings.baseUrl, settings.token),
    [settings.baseUrl, settings.token],
  );
  const ready = api.hasToken && api.baseUrl.length > 0;

  const workers = usePolling((signal) => api.getWorkers(signal), 3000, ready, [api]);
  const stats = usePolling((signal) => api.getStats(300, signal), 5000, ready, [api]);
  const jobs = usePolling((signal) => api.listJobs({ limit: 50 }, signal), 5000, ready, [api]);
  const health = usePolling((signal) => api.getHealth(signal), 10000, ready, [api]);

  function applySettings(next: ConnectionSettings) {
    const normalized = { baseUrl: next.baseUrl, token: next.token };
    if (normalized.baseUrl !== settings.baseUrl || normalized.token !== settings.token) {
      setConnectionVersion((version) => version + 1);
    }
    setSettings(normalized);
    saveSettings(normalized);
  }

  function refreshAll() {
    health.refresh();
    workers.refresh();
    stats.refresh();
    jobs.refresh();
  }

  function navigate(hash: string) {
    window.location.hash = hash;
  }

  function openConnectionPanel() {
    setConnectionOpen(true);
    setTokenFocusSignal((value) => value + 1);
  }

  const errors = [health.error, workers.error, stats.error, jobs.error];
  const connectionErrors = errors.filter((error): error is CefApiError => error instanceof CefApiError);
  const bannerError = connectionErrors.find((error) => error.kind === "unauthorized")
    ?? connectionErrors.find((error) =>
      error.kind === "network" || (error.kind === "not_found" && error.code !== "not_found"),
    )
    ?? (health.error instanceof CefApiError ? health.error : null);

  function repeatsBanner(error: unknown) {
    return error instanceof CefApiError && error.kind === bannerError?.kind && error.code === bannerError?.code;
  }

  function renderBanner() {
    if (!ready || !bannerError) return null;
    const message = toUserMessage(bannerError);
    return (
      <div className={"banner " + (bannerError.kind === "unauthorized" ? "banner--err" : "banner--warn")}>
        <div className="banner-message" role="alert">
          <div className="banner-title">{message.title}</div>
          <div className="banner-detail">{message.detail}</div>
        </div>
        <button type="button" className="btn btn--sm btn--ghost" onClick={refreshAll}>
          重新检测
        </button>
      </div>
    );
  }

  const runningJobs = stats.data?.jobs.running;
  const activeNav: NavName = route.name === "job" ? "jobs" : route.name;
  const versionLabel = health.data?.version ? "v" + health.data.version : "校园边缘算力调度";

  return (
    <div className="app-shell">
      <aside className={"app-sidebar" + (connectionOpen ? " app-sidebar--conn-open" : "")}>
        <div className="sidebar-brand">
          <span className="brand-mark" aria-hidden="true">
            CEF
          </span>
          <span className="brand-text">
            <span className="brand-name">Campus Edge Fabric</span>
            <span className="brand-version">{versionLabel}</span>
          </span>
        </div>

        <nav className="sidebar-nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <a
              key={item.hash}
              className="nav-link"
              href={item.hash}
              aria-current={activeNav === item.name ? "page" : undefined}
            >
              {item.label}
              {item.name === "jobs" && typeof runningJobs === "number" && runningJobs > 0 ? (
                <span className="nav-count">
                  {runningJobs}
                  <span className="visually-hidden"> 个作业进行中</span>
                </span>
              ) : null}
            </a>
          ))}
        </nav>

        <div className="sidebar-foot">
          <ConnectionBar
            settings={settings}
            health={health}
            onApply={applySettings}
            probeError={bannerError}
            authenticated={workers.data !== null || stats.data !== null || jobs.data !== null}
            open={connectionOpen}
            onOpenChange={setConnectionOpen}
            focusTokenSignal={tokenFocusSignal}
          />
        </div>
      </aside>

      <main className="app-main" ref={mainRef} tabIndex={-1} aria-label={ROUTE_TITLES[route.name]}>
        <header className="page-head">
          <div className="page-head-text">
            <h1 className="page-title">{ROUTE_TITLES[route.name]}</h1>
            <p className="page-desc">{ROUTE_DESCRIPTIONS[route.name]}</p>
          </div>
          {ready && (route.name === "devices" || route.name === "jobs") ? (
            <div className="page-actions">
              <button type="button" className="btn btn--sm btn--ghost" onClick={refreshAll}>
                刷新数据
              </button>
            </div>
          ) : null}
        </header>

        {renderBanner()}

        {!ready ? (
          <EmptyBox
            title="连接到算力池"
            hint="向队长获取访问 Token，填入左下角「连接设置」后点击「应用」。"
            action={
              <button type="button" className="btn btn--sm btn--primary" onClick={openConnectionPanel}>
                填写访问 Token
              </button>
            }
          />
        ) : route.name === "devices" ? (
          <>
            <StatsStrip stats={stats} health={health} onRefresh={stats.refresh} hideError={repeatsBanner(stats.error)} />
            <WorkerPanel workers={workers} api={api} onRefresh={workers.refresh} hideError={repeatsBanner(workers.error)} />
          </>
        ) : route.name === "submit" ? (
          <JobChat key={connectionVersion} api={api} token={settings.token} />
        ) : route.name === "jobs" ? (
          <JobList
            jobs={jobs}
            onRefresh={jobs.refresh}
            hideError={repeatsBanner(jobs.error)}
          />
        ) : (
          <JobDetail
            key={route.jobId}
            api={api}
            jobId={route.jobId}
            onBack={() => navigate("#/jobs")}
            connectionError={bannerError}
          />
        )}

        <footer className="app-footer">
          <span>Campus Edge Fabric · 边缘算力调度仪表盘</span>
        </footer>
      </main>
    </div>
  );
}
