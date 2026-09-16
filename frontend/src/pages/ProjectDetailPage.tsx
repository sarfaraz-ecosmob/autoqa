import { useCallback, useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import * as api from "../api/client";

interface Project {
  id: string;
  name: string;
  base_url: string;
  description: string;
  authorization_confirmed: boolean;
}

interface PageRow {
  id: string;
  url: string;
  title: string;
  depth: number;
  status_code: number | null;
  components: Record<string, number>;
}

interface ApiRow {
  id: string;
  method: string;
  path: string;
  group: string;
  source: string;
  auth_required: boolean;
}

interface PlanSection {
  section: string;
  content: string;
  priority: string;
  category: string;
}

interface TestPlan {
  version: number;
  objective: string;
  sections: PlanSection[];
  generated_by: string;
  approved: boolean;
}

interface TestCase {
  id: string;
  ref: string;
  scenario: string;
  module: string;
  category: string;
  priority: string;
  expected_result: string;
  kind: string;
  enabled: boolean;
  approved: boolean;
}

const TABS = ["overview", "discovery", "apis", "test-plan", "test-cases", "executions", "quality", "reports"] as const;
type Tab = (typeof TABS)[number];

interface RunRow {
  id: string;
  label: string;
  status: string;
  browsers: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

interface A11yRow {
  id: string;
  page_url: string;
  violation_count: number;
  by_severity: Record<string, number>;
  violations: { rule: string; severity: string; help: string; count: number }[];
}

interface ReportRow {
  id: string;
  format: string;
  status: string;
  test_run_id: string | null;
  meta: { size_bytes?: number; pass_rate?: number; failed?: number; executions?: number };
  created_at: string;
}

interface PerfRow {
  id: string;
  scenario: string;
  concurrent_users: number;
  duration_seconds: number;
  p50_ms: number;
  p90_ms: number;
  p95_ms: number;
  p99_ms: number;
  throughput_rps: number;
  error_rate: number;
  meta: { threshold_breached?: boolean; avg_ms?: number; total_requests?: number };
  created_at: string;
}

const PRIORITY_STYLES: Record<string, string> = {
  critical: "bg-red-500/10 text-red-400",
  high: "bg-orange-500/10 text-orange-400",
  medium: "bg-yellow-500/10 text-yellow-400",
  low: "bg-slate-500/10 text-slate-400",
};

export default function ProjectDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("overview");
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  // discovery
  const [pages, setPages] = useState<PageRow[]>([]);
  const [scanState, setScanState] = useState<string>("");
  const [scanControls, setScanControls] = useState({ max_depth: 2, max_urls: 50 });

  // apis
  const [apis, setApis] = useState<ApiRow[]>([]);

  // test plan
  const [plan, setPlan] = useState<TestPlan | null>(null);

  // test cases
  const [cases, setCases] = useState<TestCase[]>([]);
  const [selectedRefs, setSelectedRefs] = useState<Set<string>>(new Set());

  // runs
  const [runs, setRuns] = useState<RunRow[]>([]);

  // quality (a11y + perf)
  const [a11y, setA11y] = useState<A11yRow[]>([]);
  const [a11yState, setA11yState] = useState("");
  const [perf, setPerf] = useState<PerfRow[]>([]);
  const [perfState, setPerfState] = useState("");
  const [perfConfig, setPerfConfig] = useState({
    path: "/",
    concurrent_users: 2,
    duration_seconds: 10,
    requests_per_second: 5,
    max_response_time_ms: 3000,
  });

  // reports
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [reportState, setReportState] = useState("");
  const [reportReq, setReportReq] = useState<{ format: string; runId: string }>({
    format: "pdf",
    runId: "",
  });

  const load = useCallback(async () => {
    try {
      setProject(await api.get<Project>(`/projects/${id}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Load failed");
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function loadDiscovery() {
    try {
      setPages(await api.get<PageRow[]>(`/projects/${id}/scan/pages`));
    } catch {
      setPages([]);
    }
  }

  async function loadApis() {
    try {
      setApis(await api.get<ApiRow[]>(`/projects/${id}/analyze/apis`));
    } catch {
      setApis([]);
    }
  }

  async function loadPlan() {
    try {
      setPlan(await api.get<TestPlan>(`/projects/${id}/test-plan`));
    } catch {
      setPlan(null);
    }
  }

  async function loadCases() {
    try {
      setCases(await api.get<TestCase[]>(`/projects/${id}/test-cases`));
      setSelectedRefs(new Set());
    } catch {
      setCases([]);
    }
  }

  async function loadRuns() {
    try {
      setRuns(await api.get<RunRow[]>(`/projects/${id}/test-runs`));
    } catch {
      setRuns([]);
    }
  }

  async function startRun() {
    setError(null);
    try {
      const run = await api.post<{ id: string }>(`/projects/${id}/test-runs`, {
        label: `Run ${new Date().toLocaleTimeString()}`,
        browsers: ["chromium"],
        max_retries: 1,
      });
      await api.post(`/projects/${id}/test-runs/${run.id}/start`);
      await loadRuns();
      navigate(`/projects/${id}/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Run start failed");
    }
  }

  useEffect(() => {
    if (tab === "discovery") loadDiscovery();
    if (tab === "apis" || tab === "test-plan" || tab === "test-cases") {
      loadApis();
      if (tab === "test-plan") loadPlan();
      if (tab === "test-cases") loadCases();
    }
    if (tab === "executions") loadRuns();
    if (tab === "quality") {
      loadA11y();
      loadPerf();
    }
    if (tab === "reports") {
      loadReports();
      loadRuns();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  async function startScan() {
    setScanState("starting…");
    try {
      const res = await api.post<{ scan_id: string }>(`/projects/${id}/scan`, scanControls);
      setScanState(`scan ${res.scan_id.slice(0, 8)} running…`);
      const timer = setInterval(async () => {
        const status = await api.get<{
          status: string;
          result?: { status?: string; pages_found?: number; requests_captured?: number };
        }>(`/projects/${id}/scan/status?scan_id=${res.scan_id}`);
        if (status.status === "SUCCESS") {
          clearInterval(timer);
          setScanState(
            `completed — ${status.result?.pages_found ?? "?"} pages, ${status.result?.requests_captured ?? "?"} requests`,
          );
          loadDiscovery();
        } else if (status.status === "FAILURE") {
          clearInterval(timer);
          setScanState("failed");
        }
      }, 3000);
    } catch (err) {
      setScanState("");
      setError(err instanceof Error ? err.message : "Scan failed to start");
    }
  }

  async function runAnalysis() {
    setError(null);
    try {
      await api.post(`/projects/${id}/analyze`);
      await loadApis();
      setTab("apis");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
    }
  }

  async function generatePlan() {
    setError(null);
    try {
      setPlan(await api.post<TestPlan>(`/projects/${id}/test-plan`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Plan generation failed");
    }
  }

  async function approvePlan(approved: boolean) {
    try {
      setPlan(
        await api.patch<TestPlan>(`/projects/${id}/test-plan/approval`, { approved }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approval failed");
    }
  }

  async function generateCases() {
    setError(null);
    try {
      const res = await api.post<{ generated: number }>(`/projects/${id}/test-cases/generate`, {
        use_llm: false,
      });
      await loadCases();
      setScanState(`${res.generated} test cases generated — review and approve them`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed");
    }
  }

  async function reviewCase(ref: string, action: string, extra: object = {}) {
    try {
      await api.patch(`/projects/${id}/test-cases/${ref}`, { action, ...extra });
      await loadCases();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Review action failed");
    }
  }

  async function approveSelected() {
    try {
      await api.post(`/projects/${id}/test-cases/approve`, {
        refs: [...selectedRefs],
      });
      await loadCases();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approval failed");
    }
  }

  // ---------- Quality (a11y + perf) ----------

  async function loadA11y() {
    try {
      setA11y(await api.get<A11yRow[]>(`/projects/${id}/quality/a11y/results`));
    } catch {
      setA11y([]);
    }
  }

  async function loadPerf() {
    try {
      setPerf(await api.get<PerfRow[]>(`/projects/${id}/quality/perf/results`));
    } catch {
      setPerf([]);
    }
  }

  async function startA11yAudit() {
    setError(null);
    setA11yState("starting audit…");
    try {
      const res = await api.post<{ audit_id: string }>(`/projects/${id}/quality/a11y/audit`, {});
      const timer = setInterval(async () => {
        try {
          const s = await api.get<{ status: string; result?: { violations?: number; pages_audited?: number } }>(
            `/projects/${id}/quality/a11y/status?audit_id=${res.audit_id}`,
          );
          if (s.status === "SUCCESS") {
            clearInterval(timer);
            setA11yState(`completed — ${s.result?.pages_audited ?? "?"} pages, ${s.result?.violations ?? 0} violations`);
            loadA11y();
          } else if (s.status === "FAILURE") {
            clearInterval(timer);
            setA11yState("audit failed");
          }
        } catch {
          clearInterval(timer);
          setA11yState("");
        }
      }, 2000);
    } catch (err) {
      setA11yState("");
      setError(err instanceof Error ? err.message : "Audit failed to start");
    }
  }

  // ---------- Reports (Phase 12) ----------

  async function loadReports() {
    try {
      setReports(await api.get<ReportRow[]>(`/projects/${id}/reports`));
    } catch {
      setReports([]);
    }
  }

  async function generateReport() {
    setError(null);
    setReportState("queuing generation…");
    try {
      const res = await api.post<{ report_id: string }>(`/projects/${id}/reports`, {
        format: reportReq.format,
        test_run_id: reportReq.runId || null,
      });
      const timer = setInterval(async () => {
        try {
          const s = await api.get<ReportRow>(`/projects/${id}/reports/${res.report_id}`);
          if (s.status === "completed") {
            clearInterval(timer);
            setReportState(`ready — ${(s.meta?.size_bytes ?? 0 / 1024).toFixed(0)} KB`);
            loadReports();
          } else if (s.status === "failed") {
            clearInterval(timer);
            setReportState("generation failed");
          }
        } catch {
          clearInterval(timer);
          setReportState("");
        }
      }, 1500);
    } catch (err) {
      setReportState("");
      setError(err instanceof Error ? err.message : "Report generation failed");
    }
  }

  async function downloadReport(reportId: string) {
    setError(null);
    try {
      await api.download(`/projects/${id}/reports/${reportId}/download`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Download failed");
    }
  }

  async function startPerfRun() {
    setError(null);
    setPerfState("running load test…");
    try {
      const res = await api.post<{ perf_id: string }>(`/projects/${id}/quality/perf/run`, perfConfig);
      const timer = setInterval(async () => {
        try {
          const s = await api.get<{ status: string; result?: { p95?: number; threshold_breached?: boolean } }>(
            `/projects/${id}/quality/perf/status?perf_id=${res.perf_id}`,
          );
          if (s.status === "SUCCESS") {
            clearInterval(timer);
            const breached = s.result?.threshold_breached ? " — ⚠ threshold breached" : " — within thresholds";
            setPerfState(`completed (p95 ${s.result?.p95 ?? "?"}ms)${breached}`);
            loadPerf();
          } else if (s.status === "FAILURE") {
            clearInterval(timer);
            setPerfState("run failed");
          }
        } catch {
          clearInterval(timer);
          setPerfState("");
        }
      }, 2000);
    } catch (err) {
      setPerfState("");
      setError(err instanceof Error ? err.message : "Perf run failed to start");
    }
  }

  if (!project) {
    return (
      <div className="p-8 text-slate-400">{error ?? "Loading…"}</div>
    );
  }

  return (
    <div className="p-8 max-w-6xl">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3">
            <Link to="/" className="text-slate-500 hover:text-slate-300 text-sm">
              ← Projects
            </Link>
          </div>
          <h1 className="mt-2 text-2xl font-bold">{project.name}</h1>
          <p className="text-sm text-slate-500">{project.base_url}</p>
        </div>
        <span
          className={`rounded-full px-3 py-1 text-xs ${
            project.authorization_confirmed
              ? "bg-emerald-500/10 text-emerald-400"
              : "bg-amber-500/10 text-amber-400"
          }`}
        >
          {project.authorization_confirmed ? "authorized" : "authorization unconfirmed"}
        </span>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="mb-6 flex gap-1 border-b border-slate-800">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2.5 text-sm capitalize transition-colors ${
              tab === t
                ? "border-b-2 border-brand-500 text-brand-400 font-medium"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {t.replace("-", " ")}
          </button>
        ))}
      </div>

      {/* ---------- Overview ---------- */}
      {tab === "overview" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-6">
            <h3 className="font-semibold mb-2">Description</h3>
            <p className="text-sm text-slate-400">
              {project.description || "No description provided."}
            </p>
          </div>
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-6">
            <h3 className="font-semibold mb-3">Pipeline</h3>
            <ol className="space-y-2 text-sm text-slate-400">
              <li>1. Discover — scan the site (Discovery tab)</li>
              <li>2. Analyze — frontend stack + API inventory (APIs tab)</li>
              <li>3. Plan — generate the test plan (Test Plan tab)</li>
              <li>4. Generate &amp; review test cases (Test Cases tab)</li>
              <li>5. Execute — coming in Phase 6</li>
            </ol>
          </div>
        </div>
      )}

      {/* ---------- Discovery ---------- */}
      {tab === "discovery" && (
        <div className="space-y-4">
          {!project.authorization_confirmed ? (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-6 text-sm text-amber-200">
              Authorization must be confirmed before scanning. Edit the project to confirm, or
              re-create it with the authorization checkbox ticked.
            </div>
          ) : (
            <div className="rounded-xl border border-slate-800 bg-slate-900 p-6">
              <div className="flex items-end gap-4">
                <div>
                  <label className="block text-sm font-medium text-slate-300 mb-1.5">
                    Max depth
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={scanControls.max_depth}
                    onChange={(e) =>
                      setScanControls({ ...scanControls, max_depth: Number(e.target.value) })
                    }
                    className="w-24 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-300 mb-1.5">
                    Max URLs
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={1000}
                    value={scanControls.max_urls}
                    onChange={(e) =>
                      setScanControls({ ...scanControls, max_urls: Number(e.target.value) })
                    }
                    className="w-24 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm"
                  />
                </div>
                <button
                  onClick={startScan}
                  disabled={scanState.includes("running")}
                  className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-5 py-2 text-sm font-semibold"
                >
                  Start scan
                </button>
              </div>
              {scanState && (
                <p className="mt-3 text-sm text-slate-400">
                  {scanState}
                  {scanState.includes("running") && (
                    <span className="ml-2 inline-block h-2 w-2 animate-pulse rounded-full bg-brand-500" />
                  )}
                </p>
              )}
            </div>
          )}

          <div className="rounded-xl border border-slate-800 bg-slate-900">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
              <h3 className="font-semibold">Discovered pages ({pages.length})</h3>
              <button onClick={runAnalysis} className="text-sm text-brand-400 hover:text-brand-300">
                Run architecture analysis →
              </button>
            </div>
            {pages.length === 0 ? (
              <p className="px-6 py-8 text-sm text-slate-500">No pages discovered yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-800">
                    <th className="px-6 py-3">URL</th>
                    <th className="px-6 py-3">Title</th>
                    <th className="px-6 py-3">Depth</th>
                    <th className="px-6 py-3">Status</th>
                    <th className="px-6 py-3">Components</th>
                  </tr>
                </thead>
                <tbody>
                  {pages.map((p) => (
                    <tr key={p.id} className="border-b border-slate-800/50 last:border-0">
                      <td className="px-6 py-3 text-slate-200 font-mono text-xs">{p.url}</td>
                      <td className="px-6 py-3 text-slate-400">{p.title || "—"}</td>
                      <td className="px-6 py-3 text-slate-400">{p.depth}</td>
                      <td className="px-6 py-3">
                        <span
                          className={`rounded px-1.5 py-0.5 text-xs ${
                            p.status_code === 200
                              ? "bg-emerald-500/10 text-emerald-400"
                              : "bg-slate-500/10 text-slate-400"
                          }`}
                        >
                          {p.status_code ?? "?"}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-xs text-slate-500">
                        {Object.entries(p.components)
                          .filter(([, v]) => v > 0)
                          .map(([k, v]) => `${k}:${v}`)
                          .join(" · ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* ---------- APIs ---------- */}
      {tab === "apis" && (
        <div className="rounded-xl border border-slate-800 bg-slate-900">
          <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
            <h3 className="font-semibold">API inventory ({apis.length})</h3>
            <button onClick={runAnalysis} className="text-sm text-brand-400 hover:text-brand-300">
              Re-run analysis →
            </button>
          </div>
          {apis.length === 0 ? (
            <p className="px-6 py-8 text-sm text-slate-500">
              No APIs discovered yet. Run a scan (Discovery tab), then “Run architecture analysis”.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500 border-b border-slate-800">
                  <th className="px-6 py-3">Method</th>
                  <th className="px-6 py-3">Path</th>
                  <th className="px-6 py-3">Group</th>
                  <th className="px-6 py-3">Source</th>
                </tr>
              </thead>
              <tbody>
                {apis.map((a) => (
                  <tr key={a.id} className="border-b border-slate-800/50 last:border-0">
                    <td className="px-6 py-3">
                      <span className="rounded bg-brand-500/10 px-1.5 py-0.5 text-xs font-mono text-brand-400">
                        {a.method}
                      </span>
                    </td>
                    <td className="px-6 py-3 font-mono text-xs text-slate-200">{a.path}</td>
                    <td className="px-6 py-3 text-slate-400 capitalize">{a.group}</td>
                    <td className="px-6 py-3 text-xs text-slate-500">{a.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ---------- Test Plan ---------- */}
      {tab === "test-plan" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-6 flex items-center justify-between">
            <div>
              <h3 className="font-semibold">Test plan</h3>
              {plan && (
                <p className="text-sm text-slate-500 mt-0.5">
                  v{plan.version} · generated by {plan.generated_by} ·{" "}
                  {plan.approved ? "approved" : "pending approval"}
                </p>
              )}
            </div>
            <div className="flex gap-2">
              <button
                onClick={generatePlan}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 px-4 py-2 text-sm font-semibold"
              >
                {plan ? "Regenerate" : "Generate plan"}
              </button>
              {plan && (
                <button
                  onClick={() => approvePlan(!plan.approved)}
                  className={`rounded-lg px-4 py-2 text-sm font-semibold ${
                    plan.approved
                      ? "bg-slate-800 text-slate-300"
                      : "bg-emerald-600 hover:bg-emerald-500"
                  }`}
                >
                  {plan.approved ? "Revoke approval" : "Approve"}
                </button>
              )}
            </div>
          </div>

          {!plan ? (
            <div className="rounded-xl border border-dashed border-slate-800 p-12 text-center text-slate-500">
              No plan yet — generate one from discovery evidence.
            </div>
          ) : (
            <>
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-6">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-1">
                  Objective
                </h4>
                <p className="text-sm text-slate-300">{plan.objective}</p>
              </div>
              <div className="space-y-2">
                {plan.sections.map((s, i) => (
                  <div
                    key={i}
                    className="rounded-xl border border-slate-800 bg-slate-900 px-5 py-4 flex items-start gap-4"
                  >
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase font-semibold ${
                        PRIORITY_STYLES[s.priority] ?? PRIORITY_STYLES.low
                      }`}
                    >
                      {s.priority}
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-slate-200">{s.section}</p>
                      <p className="text-sm text-slate-500 mt-0.5">{s.content}</p>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* ---------- Executions ---------- */}
      {tab === "executions" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-6 flex items-center justify-between">
            <div>
              <h3 className="font-semibold">Test runs ({runs.length})</h3>
              <p className="text-sm text-slate-500 mt-0.5">
                Execute approved test cases with live progress
              </p>
            </div>
            <button
              onClick={startRun}
              className="rounded-lg bg-brand-600 hover:bg-brand-500 px-4 py-2 text-sm font-semibold"
            >
              ▶ Start run
            </button>
          </div>
          {runs.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-800 p-12 text-center text-slate-500">
              No runs yet — approve test cases, then start a run.
            </div>
          ) : (
            <div className="space-y-2">
              {runs.map((r) => (
                <div key={r.id} className="rounded-xl border border-slate-800 bg-slate-900 px-5 py-4 flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-slate-200">{r.label}</p>
                    <p className="text-xs text-slate-500">
                      {new Date(r.created_at).toLocaleString()} · browsers: {(r.browsers ?? []).join(", ")}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className={`rounded-full px-2.5 py-0.5 text-xs capitalize ${
                      r.status === "completed" ? "bg-emerald-500/10 text-emerald-400"
                      : r.status === "running" ? "bg-brand-500/10 text-brand-400"
                      : "bg-slate-800 text-slate-400"}`}>
                      {r.status}
                    </span>
                    <Link
                      to={`/projects/${id}/runs/${r.id}`}
                      className="text-sm text-brand-400 hover:text-brand-300"
                    >
                      View live →
                    </Link>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ---------- Test Cases ---------- */}
      {tab === "test-cases" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-6 flex items-center justify-between">
            <div>
              <h3 className="font-semibold">
                Test cases ({cases.length})
              </h3>
              <p className="text-sm text-slate-500 mt-0.5">
                {cases.filter((c) => c.approved).length} approved ·{" "}
                {cases.filter((c) => !c.approved).length} pending review
              </p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={generateCases}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 px-4 py-2 text-sm font-semibold"
              >
                {cases.length ? "Regenerate" : "Generate cases"}
              </button>
              {selectedRefs.size > 0 && (
                <button
                  onClick={approveSelected}
                  className="rounded-lg bg-emerald-600 hover:bg-emerald-500 px-4 py-2 text-sm font-semibold"
                >
                  Approve selected ({selectedRefs.size})
                </button>
              )}
              {cases.length > 0 && cases.some((c) => !c.approved) && (
                <button
                  onClick={async () => {
                    await api.post(`/projects/${id}/test-cases/approve`, { refs: [] });
                    await loadCases();
                  }}
                  className="rounded-lg bg-slate-800 hover:bg-slate-700 px-4 py-2 text-sm font-semibold"
                >
                  Approve all
                </button>
              )}
            </div>
          </div>

          {cases.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-800 p-12 text-center text-slate-500">
              No test cases yet — generate them from discovery data.
            </div>
          ) : (
            <div className="space-y-2">
              {cases.map((c) => (
                <div
                  key={c.id}
                  className="rounded-xl border border-slate-800 bg-slate-900 px-5 py-4"
                >
                  <div className="flex items-start gap-3">
                    <input
                      type="checkbox"
                      checked={selectedRefs.has(c.ref)}
                      onChange={(e) => {
                        const next = new Set(selectedRefs);
                        if (e.target.checked) next.add(c.ref);
                        else next.delete(c.ref);
                        setSelectedRefs(next);
                      }}
                      className="mt-1 h-4 w-4 accent-brand-500"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs text-brand-400">{c.ref}</span>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] uppercase font-semibold ${
                            PRIORITY_STYLES[c.priority] ?? PRIORITY_STYLES.low
                          }`}
                        >
                          {c.priority}
                        </span>
                        <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] uppercase text-slate-400">
                          {c.kind}
                        </span>
                        <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
                          {c.module}
                        </span>
                        {c.approved && (
                          <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-400">
                            approved
                          </span>
                        )}
                        {!c.enabled && (
                          <span className="rounded bg-slate-500/10 px-1.5 py-0.5 text-[10px] text-slate-500">
                            disabled
                          </span>
                        )}
                      </div>
                      <p className="mt-1.5 text-sm text-slate-200">{c.scenario}</p>
                      <p className="mt-1 text-xs text-slate-500">
                        <span className="text-slate-600">Expected:</span> {c.expected_result}
                      </p>
                      <div className="mt-2 flex gap-2">
                        {!c.approved && (
                          <button
                            onClick={() => reviewCase(c.ref, "approve")}
                            className="rounded bg-emerald-600/20 px-2.5 py-1 text-xs text-emerald-400 hover:bg-emerald-600/30"
                          >
                            Approve
                          </button>
                        )}
                        <button
                          onClick={() => {
                            const next = window.prompt("Edit expected result", c.expected_result);
                            if (next !== null) reviewCase(c.ref, "approve", { expected_result: next });
                          }}
                          className="rounded bg-slate-800 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-700"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => reviewCase(c.ref, "duplicate")}
                          className="rounded bg-slate-800 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-700"
                        >
                          Duplicate
                        </button>
                        <button
                          onClick={() => reviewCase(c.ref, c.enabled ? "disable" : "enable")}
                          className="rounded bg-slate-800 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-700"
                        >
                          {c.enabled ? "Disable" : "Enable"}
                        </button>
                        <button
                          onClick={() => reviewCase(c.ref, "reject")}
                          className="rounded bg-red-600/10 px-2.5 py-1 text-xs text-red-400 hover:bg-red-600/20"
                        >
                          Reject
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {/* ---------- Quality (a11y + perf) ---------- */}
      {tab === "quality" && (
        <div className="space-y-4">
          {!project.authorization_confirmed && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-200">
              Authorization must be confirmed before running accessibility or performance modules.
            </div>
          )}

          {/* Accessibility */}
          <div className="rounded-xl border border-slate-800 bg-slate-900">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
              <div>
                <h3 className="font-semibold">Accessibility (WCAG)</h3>
                <p className="text-sm text-slate-500 mt-0.5">Label, alt-text, heading, and ARIA checks on discovered pages</p>
              </div>
              <button
                onClick={startA11yAudit}
                disabled={!project.authorization_confirmed || a11yState.includes("audit")}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm font-semibold"
              >
                Run accessibility audit
              </button>
            </div>
            {a11yState && (
              <p className="px-6 py-3 text-sm text-slate-400">
                {a11yState}
                {a11yState.includes("audit…") || a11yState.includes("starting") ? (
                  <span className="ml-2 inline-block h-2 w-2 animate-pulse rounded-full bg-brand-500" />
                ) : null}
              </p>
            )}
            {a11y.length === 0 ? (
              <p className="px-6 py-8 text-sm text-slate-500">No accessibility results yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-800">
                    <th className="px-6 py-3">Page</th>
                    <th className="px-6 py-3">Violations</th>
                    <th className="px-6 py-3">By severity</th>
                    <th className="px-6 py-3">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {a11y.map((r) => (
                    <tr key={r.id} className="border-b border-slate-800/50 last:border-0">
                      <td className="px-6 py-3 font-mono text-xs text-slate-200 max-w-72 truncate">{r.page_url}</td>
                      <td className="px-6 py-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                          r.violation_count === 0 ? "bg-emerald-500/10 text-emerald-400"
                          : "bg-red-500/10 text-red-400"}`}>
                          {r.violation_count}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-xs text-slate-400">
                        {Object.entries(r.by_severity).map(([sev, n]) => `${sev}:${n}`).join(" · ") || "—"}
                      </td>
                      <td className="px-6 py-3 text-xs text-slate-500 max-w-96">
                        {r.violations.slice(0, 3).map((v) => `${v.rule} (${v.count})`).join(", ")}
                        {r.violations.length > 3 ? ` +${r.violations.length - 3} more` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Performance */}
          <div className="rounded-xl border border-slate-800 bg-slate-900">
            <div className="px-6 py-4 border-b border-slate-800">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold">Performance (smoke load)</h3>
                  <p className="text-sm text-slate-500 mt-0.5">Bounded rate-limited run — production-scale loads are not permitted from the UI</p>
                </div>
                <button
                  onClick={startPerfRun}
                  disabled={!project.authorization_confirmed || perfState.includes("load test")}
                  className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm font-semibold"
                >
                  Run load test
                </button>
              </div>
              <div className="mt-4 flex flex-wrap gap-3">
                {([
                  ["path", "text", "Path"],
                  ["concurrent_users", "number", "Users"],
                  ["duration_seconds", "number", "Duration (s)"],
                  ["requests_per_second", "number", "RPS"],
                  ["max_response_time_ms", "number", "Max p95 (ms)"],
                ] as const).map(([key, type, label]) => (
                  <label key={key} className="text-xs text-slate-400">
                    <span className="block mb-1">{label}</span>
                    <input
                      type={type}
                      value={perfConfig[key] as string | number}
                      onChange={(e) =>
                        setPerfConfig({
                          ...perfConfig,
                          [key]: type === "number" ? Number(e.target.value) : e.target.value,
                        })
                      }
                      className="w-24 rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-sm"
                    />
                  </label>
                ))}
              </div>
              {perfState && <p className="mt-3 text-sm text-slate-400">{perfState}</p>}
            </div>
            {perf.length === 0 ? (
              <p className="px-6 py-8 text-sm text-slate-500">No performance results yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-800">
                    <th className="px-6 py-3">When</th>
                    <th className="px-6 py-3">Path</th>
                    <th className="px-6 py-3">Requests</th>
                    <th className="px-6 py-3">p50 / p95 / p99 (ms)</th>
                    <th className="px-6 py-3">RPS</th>
                    <th className="px-6 py-3">Errors</th>
                    <th className="px-6 py-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {perf.map((r) => (
                    <tr key={r.id} className="border-b border-slate-800/50 last:border-0">
                      <td className="px-6 py-3 text-xs text-slate-400">{new Date(r.created_at).toLocaleString()}</td>
                      <td className="px-6 py-3 font-mono text-xs text-slate-200">{r.scenario}</td>
                      <td className="px-6 py-3 text-slate-400">{r.meta?.total_requests ?? "—"}</td>
                      <td className="px-6 py-3 text-slate-300">{r.p50_ms} / {r.p95_ms} / {r.p99_ms}</td>
                      <td className="px-6 py-3 text-slate-400">{r.throughput_rps}</td>
                      <td className="px-6 py-3 text-slate-400">{(r.error_rate * 100).toFixed(1)}%</td>
                      <td className="px-6 py-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                          r.meta?.threshold_breached ? "bg-red-500/10 text-red-400" : "bg-emerald-500/10 text-emerald-400"}`}>
                          {r.meta?.threshold_breached ? "breached" : "ok"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
      {/* ---------- Reports (Phase 12) ---------- */}
      {tab === "reports" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-6">
            <h3 className="font-semibold">Generate report</h3>
            <p className="text-sm text-slate-500 mt-0.5">
              CSV · Excel · PDF · HTML (with embedded evidence screenshots) · JSON — includes plan,
              executions, defects, security, performance, accessibility, and recommendations
            </p>
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <label className="text-xs text-slate-400">
                <span className="block mb-1">Format</span>
                <select
                  value={reportReq.format}
                  onChange={(e) => setReportReq({ ...reportReq, format: e.target.value })}
                  className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm"
                >
                  {["pdf", "html", "xlsx", "csv", "json"].map((f) => (
                    <option key={f} value={f}>{f.toUpperCase()}</option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-slate-400">
                <span className="block mb-1">Scope</span>
                <select
                  value={reportReq.runId}
                  onChange={(e) => setReportReq({ ...reportReq, runId: e.target.value })}
                  className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm"
                >
                  <option value="">Whole project</option>
                  {runs.map((r) => (
                    <option key={r.id} value={r.id}>{r.label}</option>
                  ))}
                </select>
              </label>
              <button
                onClick={generateReport}
                disabled={reportState.includes("generation…")}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-5 py-2 text-sm font-semibold"
              >
                Generate
              </button>
              {reportState && <span className="text-sm text-slate-400">{reportState}</span>}
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900">
            <div className="px-6 py-4 border-b border-slate-800">
              <h3 className="font-semibold">Reports ({reports.length})</h3>
            </div>
            {reports.length === 0 ? (
              <p className="px-6 py-8 text-sm text-slate-500">No reports generated yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-800">
                    <th className="px-6 py-3">Generated</th>
                    <th className="px-6 py-3">Format</th>
                    <th className="px-6 py-3">Scope</th>
                    <th className="px-6 py-3">Executions</th>
                    <th className="px-6 py-3">Status</th>
                    <th className="px-6 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {reports.map((r) => (
                    <tr key={r.id} className="border-b border-slate-800/50 last:border-0">
                      <td className="px-6 py-3 text-xs text-slate-400">{new Date(r.created_at).toLocaleString()}</td>
                      <td className="px-6 py-3">
                        <span className="rounded bg-brand-500/10 px-1.5 py-0.5 text-xs font-mono uppercase text-brand-400">
                          {r.format}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-xs text-slate-400">
                        {r.test_run_id ? runs.find((x) => x.id === r.test_run_id)?.label ?? "run" : "whole project"}
                      </td>
                      <td className="px-6 py-3 text-xs text-slate-400">
                        {r.meta?.executions != null ? `${r.meta.executions} (${r.meta.failed ?? 0} failed)` : "—"}
                      </td>
                      <td className="px-6 py-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-semibold capitalize ${
                          r.status === "completed" ? "bg-emerald-500/10 text-emerald-400"
                          : r.status === "failed" ? "bg-red-500/10 text-red-400"
                          : "bg-slate-500/10 text-slate-400"}`}>
                          {r.status}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-right">
                        {r.status === "completed" && (
                          <button
                            onClick={() => downloadReport(r.id)}
                            className="rounded bg-slate-800 px-3 py-1.5 text-xs text-brand-400 hover:bg-slate-700"
                          >
                            Download
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
