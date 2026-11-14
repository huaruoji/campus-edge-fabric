import { useEffect, useMemo, useState } from "react";
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

function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, "");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "submit") return { name: "submit" };
  if (parts[0] === "jobs" && parts[1]) return { name: "job", jobId: decodeURIComponent(parts[1]) };
  if (parts[0] === "jobs") return { name: "jobs" };
  return { name: "devices" };
}

export default function App() {
  const [settings, setSettings] = useState<ConnectionSettings>(() => loadSettings());
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    function onHashChange() {
      setRoute(parseRoute(window.location.hash));
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    const titles: Record<Route["name"], string> = {
      devices: "设备面板",
      submit: "提交任务",
      jobs: "任务列表",
      job: "任务详情",
    };
    document.title = titles[route.name] + " · Campus Edge Fabric";
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

  const probeError = health.error ?? workers.error ?? stats.error ?? jobs.error;
  const bannerError =
    probeError instanceof CefApiError &&
    (probeError.kind === "unauthorized" || probeError.kind === "network" || probeError.kind === "server")
      ? probeError
      : null;

  function renderBanner() {
    if (!api.hasToken) {
      return (
        <div className="banner banner--info">
          <div className="banner-title">请先配置访问 Token</div>
          <div className="banner-detail">
            在顶部填写与后端 CEF_TOKEN 一致的 Token，点「应用」后本页会开始轮询 /api/v1 接口；Token 只保存在本机。
          </div>
        </div>
      );
    }
    // /health 在后端是免鉴权的，所以 token 错误只能从需要鉴权的接口上看出来。
    if (!bannerError) return null;
    const message = toUserMessage(bannerError);
    return (
      <div className={"banner " + (bannerError.kind === "unauthorized" ? "banner--err" : "banner--warn")}>
        <div className="banner-title">{message.title}</div>
        <div className="banner-detail">{message.detail}</div>
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
      <ConnectionBar settings={settings} health={health} onApply={applySettings} probeError={probeError} />

      <StatsStrip
        stats={stats}
        health={health}
        onRefresh={stats.refresh}
        hideError={Boolean(bannerError)}
      />

      <nav className="tabs" aria-label="主导航">
        <a className={"tab" + (activeTab === "devices" ? " tab--active" : "")} href="#/devices">
          设备面板
        </a>
        <a className={"tab" + (activeTab === "submit" ? " tab--active" : "")} href="#/submit">
          提交任务
        </a>
        <a className={"tab" + (activeTab === "jobs" ? " tab--active" : "")} href="#/jobs">
          任务列表
          {typeof runningJobs === "number" && runningJobs > 0 ? (
            <span className="tab-count">{runningJobs}</span>
          ) : null}
        </a>
      </nav>

      <main className="app-main">
        {renderBanner()}

        {!ready ? (
          <EmptyBox
            title="尚未连接到 Coordinator"
            hint="填写 Token 并应用后，即可查看设备、提交任务与查看任务详情。若后端尚未启动，页面会显示明确的连接失败提示，而不是白屏。"
          />
        ) : route.name === "devices" ? (
          <WorkerPanel workers={workers} onRefresh={workers.refresh} hideError={Boolean(bannerError)} />
        ) : route.name === "submit" ? (
          <JobSubmitForm
            api={api}
            workloads={workloads}
            onSubmitted={(jobId) => navigate("#/jobs/" + encodeURIComponent(jobId))}
          />
        ) : route.name === "jobs" ? (
          <JobList
            jobs={jobs}
            onOpen={(jobId) => navigate("#/jobs/" + encodeURIComponent(jobId))}
            onRefresh={jobs.refresh}
            hideError={Boolean(bannerError)}
          />
        ) : (
          <JobDetail
            key={route.jobId}
            api={api}
            jobId={route.jobId}
            onBack={() => navigate("#/jobs")}
            hideError={Boolean(bannerError)}
          />
        )}

        <footer className="app-footer">
          <span>Campus Edge Fabric · 边缘算力调度仪表盘</span>
          <span>接口契约 docs/api.md（v1 冻结）</span>
          <span>轮询：设备 3s · 统计 5s · 任务 5s · 详情 2s</span>
          <span className="mono">{api.baseUrl || "未配置地址"}/api/v1</span>
        </footer>
      </main>
    </div>
  );
}
