import { useEffect, useMemo, useRef, useState } from "react";
import type { ConnectionSettings } from "./api";
import { CefApiError, createApiClient, loadSettings, saveSettings, toUserMessage } from "./api";
import { usePolling } from "./hooks/usePolling";
import { ConnectionBar } from "./components/ConnectionBar";
import { StatsStrip } from "./components/StatsStrip";
import { WorkerPanel } from "./components/WorkerPanel";
import { JobSubmitForm } from "./components/JobSubmitForm";
import { JobList } from "./components/JobList";
import { JobDetail } from "./components/JobDetail";
import { EmptyBox } from "./components/ui";

type Route = { name: "devices" } | { name: "submit" } | { name: "jobs" } | { name: "job"; jobId: string };

const ROUTE_TITLES: Record<Route["name"], string> = {
  devices: "设备面板",
  submit: "提交任务",
  jobs: "任务列表",
  job: "任务详情",
};

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
  const workloads = usePolling((signal) => api.getWorkloads(signal), 20000, ready, [api]);
  const health = usePolling((signal) => api.getHealth(signal), 10000, ready, [api]);

  function applySettings(next: ConnectionSettings) {
    const normalized = { baseUrl: next.baseUrl, token: next.token };
    setSettings(normalized);
    saveSettings(normalized);
  }

  function refreshAll() {
    health.refresh();
    workers.refresh();
    stats.refresh();
    jobs.refresh();
    workloads.refresh();
  }

  function navigate(hash: string) {
    window.location.hash = hash;
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
  const activeTab = route.name === "job" ? "jobs" : route.name;

  return (
    <div className="app">
      <ConnectionBar
        settings={settings}
        health={health}
        onApply={applySettings}
        probeError={bannerError}
        authenticated={workers.data !== null || stats.data !== null || jobs.data !== null}
      />

      <nav className="tabs" aria-label="主导航">
        <a className={"tab" + (activeTab === "devices" ? " tab--active" : "")} href="#/devices" aria-current={activeTab === "devices" ? "page" : undefined}>
          设备面板
        </a>
        <a className={"tab" + (activeTab === "submit" ? " tab--active" : "")} href="#/submit" aria-current={activeTab === "submit" ? "page" : undefined}>
          提交任务
        </a>
        <a className={"tab" + (activeTab === "jobs" ? " tab--active" : "")} href="#/jobs" aria-current={activeTab === "jobs" ? "page" : undefined}>
          任务列表
          {typeof runningJobs === "number" && runningJobs > 0 ? (
            <span className="tab-count">{runningJobs}</span>
          ) : null}
        </a>
      </nav>

      <main className="app-main" ref={mainRef} tabIndex={-1} aria-label={ROUTE_TITLES[route.name]}>
        {renderBanner()}

        {!ready ? (
          <EmptyBox
            title="连接到算力池"
            hint="向队长获取访问 Token，填入上方连接栏后点击「应用」。"
            action={
              <button type="button" className="btn btn--sm btn--primary" onClick={() => document.getElementById("cef-access-token")?.focus()}>
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
          <JobSubmitForm
            api={api}
            workloads={workloads}
            onSubmitted={(jobId) => navigate("#/jobs/" + encodeURIComponent(jobId))}
          />
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
