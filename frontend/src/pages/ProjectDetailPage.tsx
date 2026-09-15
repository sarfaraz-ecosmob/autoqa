import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
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

const TABS = ["overview", "discovery", "apis", "test-plan", "test-cases"] as const;
type Tab = (typeof TABS)[number];

const PRIORITY_STYLES: Record<string, string> = {
  critical: "bg-red-500/10 text-red-400",
  high: "bg-orange-500/10 text-orange-400",
  medium: "bg-yellow-500/10 text-yellow-400",
  low: "bg-slate-500/10 text-slate-400",
};

export default function ProjectDetailPage() {
  const { id = "" } = useParams();
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

  useEffect(() => {
    if (tab === "discovery") loadDiscovery();
    if (tab === "apis" || tab === "test-plan" || tab === "test-cases") {
      loadApis();
      if (tab === "test-plan") loadPlan();
      if (tab === "test-cases") loadCases();
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
    </div>
  );
}
