import { useCallback, useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import * as api from "../api/client";

interface Project {
  id: string;
  name: string;
  base_url: string;
  description: string;
  authorization_confirmed: boolean;
  has_credentials?: boolean;
  credentials?: Record<string, string>; // masked (••••••••)
  auth?: Record<string, unknown>; // auth flow config (masked)
}

// Auth-flow form state (Overview → Test credentials & sign-in)
interface AuthForm {
  username: string;
  password: string;
  login_url: string;
  username_selector: string;
  password_selector: string;
  submit_selector: string;
  success_assert: string;
  api_auth_header: string;
  api_auth_prefix: string;
  api_auth_header_value: string;
  token_path: string;
  token_endpoint: string;
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

const TABS = ["overview", "requirements", "discovery", "apis", "test-plan", "test-cases", "test-data", "executions", "history", "quality", "assistant", "reports", "automations"] as const;
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

interface HistoryRow {
  id: string;
  label: string;
  status: string;
  browsers: string[];
  created_at: string;
  passed: number;
  failed: number;
  skipped: number;
  other: number;
}

interface CompareResult {
  base: { label: string; total: number; passed: number; failed: number };
  target: { label: string; total: number; passed: number; failed: number };
  new_failures: { ref: string; browser: string }[];
  resolved_failures: { ref: string; browser: string }[];
  persistent_failures: { ref: string; browser: string }[];
  new_tests: { ref: string; browser: string }[];
  removed_tests: { ref: string; browser: string }[];
  duration_changes: { ref: string; browser: string; base_ms: number; target_ms: number; delta_ms: number }[];
  performance_changes: { scenario: string; base_p95_ms: number; target_p95_ms: number; delta_p95_ms: number }[];
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

interface EnvRow {
  id: string;
  name: string;
  base_url: string;
  variables: Record<string, string>;
  created_at: string;
}

interface DatasetRow {
  id: string;
  name: string;
  kind: string;
  generator: string;
  generator_params: Record<string, unknown>;
  values: Record<string, string>;
  secret_keys: string[];
  environment_id: string | null;
  is_active: boolean;
  created_at: string;
}

interface ChatMsg {
  role: "user" | "assistant";
  text: string;
  intent?: string;
  grounded?: boolean;
}

interface ScheduleRow {
  id: string;
  name: string;
  cron: string;
  browsers: string[];
  parallelism: number;
  is_active: boolean;
  last_run_id: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
}

interface RequirementRow {
  id: string;
  external_id: string;
  source: string;
  title: string;
  description: string;
  priority: string;
  status: string;
  tags: string[];
  coverage_count?: number;
}

interface TraceRow {
  external_id: string;
  title: string;
  priority: string;
  status: string;
  cases: { ref: string; scenario: string; kind: string; approved: boolean }[];
  results: string[];
  defect_count: number;
}

interface TraceSummary {
  requirements: number;
  covered: number;
  uncovered: number;
  pass_rate: number | null;
}

interface RequirementDetail extends RequirementRow {
  cases: { ref: string; scenario: string; kind: string; approved: boolean; enabled: boolean }[];
}

interface WebhookRow {
  id: string;
  name: string;
  masked_url: string;
  events: string[];
  is_active: boolean;
  last_status: string;
  last_delivery_at: string | null;
}

interface FlakyRow {
  test_case_id: string;
  runs_observed: number;
  pass_rate: number;
  retried_pass: boolean;
  alternating: boolean;
  flaky_score: number;
  classification: string;
}

interface VisualCheckRow {
  id: string;
  test_case_id: string;
  execution_id: string;
  browser: string;
  status: string;
  diff_percent: number;
  created_at: string;
}

const PRIORITY_STYLES: Record<string, string> = {
  critical: "bg-red-50 text-red-600",
  high: "bg-orange-500/10 text-orange-400",
  medium: "bg-yellow-500/10 text-yellow-400",
  low: "bg-slate-100 text-slate-500",
};

export default function ProjectDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("overview");
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  // credentials & auth flow (Overview card)
  const emptyAuth: AuthForm = {
    username: "",
    password: "",
    login_url: "",
    username_selector: "input[name='username']",
    password_selector: "input[type='password']",
    submit_selector: "button[type='submit']",
    success_assert: "url_not_contains:login",
    api_auth_header: "Authorization",
    api_auth_prefix: "Bearer ",
    api_auth_header_value: "",
    token_path: "token",
    token_endpoint: "",
  };
  const [credForm, setCredForm] = useState<AuthForm>(emptyAuth);
  const [credBusy, setCredBusy] = useState(false);
  const [credMsg, setCredMsg] = useState<string | null>(null);

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

  // history (Phase 13)
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [compareBase, setCompareBase] = useState("");
  const [compareTarget, setCompareTarget] = useState("");
  const [compareResult, setCompareResult] = useState<CompareResult | null>(null);
  const [compareBusy, setCompareBusy] = useState(false);

  // assistant (Phase 14, §22)
  const [chat, setChat] = useState<ChatMsg[]>([
    {
      role: "assistant",
      text: "Hi! Ask me about failures, defects by module, API latency, security findings, accessibility, run comparison, or a project summary — I answer from your actual QA data.",
    },
  ]);
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);

  // test data (Phase 14, §17)
  const [envs, setEnvs] = useState<EnvRow[]>([]);
  const [datasets, setDatasets] = useState<DatasetRow[]>([]);
  const [resolvedPreview, setResolvedPreview] = useState<{ variables: Record<string, string>; count: number } | null>(null);
  const [envForm, setEnvForm] = useState<{ name: string; base_url: string; varsText: string }>({ name: "", base_url: "", varsText: "" });
  const [dsForm, setDsForm] = useState<{ name: string; kind: string; generator: string; valuesText: string; envId: string }>({
    name: "",
    kind: "static",
    generator: "",
    valuesText: "",
    envId: "",
  });

  // requirements & traceability (PLAN V2.1)
  const [requirements, setRequirements] = useState<RequirementRow[]>([]);
  const [trace, setTrace] = useState<{ rows: TraceRow[]; summary: TraceSummary } | null>(null);
  const [reqText, setReqText] = useState("");
  const [reqBusy, setReqBusy] = useState(false);
  const [reqMsg, setReqMsg] = useState<string | null>(null);
  const [selectedReqs, setSelectedReqs] = useState<Set<string>>(new Set());

  // automations (Tier-1): schedules + webhooks + flaky + visual
  const [schedules, setSchedules] = useState<ScheduleRow[]>([]);
  const [webhooks, setWebhooks] = useState<WebhookRow[]>([]);
  const [flaky, setFlaky] = useState<FlakyRow[]>([]);
  const [visualChecks, setVisualChecks] = useState<VisualCheckRow[]>([]);
  const [schedForm, setSchedForm] = useState({ name: "", cron: "0 2 * * *", browsers: "chromium", parallelism: 2 });
  const [hookForm, setHookForm] = useState({ name: "", url: "" });
  const [recorderText, setRecorderText] = useState("");
  const [recorderMsg, setRecorderMsg] = useState("");

  const load = useCallback(async () => {
    try {
      const p = await api.get<Project>(`/projects/${id}`);
      setProject(p);
      applyAuthDefaults(p);
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

  async function confirmAuthorization() {
    setError(null);
    try {
      await api.patch(`/projects/${id}/authorization?confirmed=true`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to confirm authorization");
    }
  }

  // ---------- Credentials & auth flow (Overview) ----------

  function applyAuthDefaults(p: Project | null) {
    if (!p) return;
    const a = (p.auth || {}) as Record<string, string>;
    const tokReq = (a.api_token_request || {}) as Record<string, string>;
    setCredForm((f) => ({
      ...f,
      login_url: a.login_url || "",
      username_selector: a.username_selector || f.username_selector,
      password_selector: a.password_selector || f.password_selector,
      submit_selector: a.submit_selector || f.submit_selector,
      success_assert: a.success_assert || f.success_assert,
      api_auth_header: a.api_auth_header ?? f.api_auth_header,
      api_auth_prefix: a.api_auth_prefix ?? f.api_auth_prefix,
      api_auth_header_value: a.api_auth_header_value || "",
      token_path: tokReq.token_path || f.token_path,
      token_endpoint: tokReq.path || f.token_endpoint,
    }));
  }

  async function saveCredentials(clear = false) {
    setCredBusy(true);
    setCredMsg(null);
    try {
      const body: Record<string, unknown> = {};
      if (clear) {
        body.clear_credentials = true;
      } else {
        const values: Record<string, string> = {};
        if (credForm.username) values.username = credForm.username;
        if (credForm.password) values.password = credForm.password;
        if (credForm.api_auth_header_value) values.api_token = credForm.api_auth_header_value;
        const auth: Record<string, unknown> = {
          login_url: credForm.login_url || null,
          username_selector: credForm.username_selector || null,
          password_selector: credForm.password_selector || null,
          submit_selector: credForm.submit_selector || null,
          success_assert: credForm.success_assert || null,
          api_auth_header: credForm.api_auth_header || "",
          api_auth_prefix: credForm.api_auth_prefix || "",
        };
        if (credForm.api_auth_header_value) auth.api_auth_header_value = credForm.api_auth_header_value;
        if (credForm.token_endpoint) {
          auth.api_token_request = {
            method: "POST",
            path: credForm.token_endpoint,
            json: { username: "{{username}}", password: "{{password}}" },
            token_path: credForm.token_path || "token",
          };
        }
        body.credentials = values;
        body.auth = auth;
      }
      const updated = await api.patch<Project>(`/projects/${id}`, body);
      setProject(updated);
      applyAuthDefaults(updated);
      setCredForm((f) => ({ ...f, username: "", password: "", api_auth_header_value: "" }));
      setCredMsg(clear ? "Credentials cleared." : "Saved — credentials are encrypted at rest.");
    } catch (err) {
      setCredMsg(err instanceof Error ? err.message : "Save failed");
    } finally {
      setCredBusy(false);
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
    if (tab === "history") {
      loadHistory();
    }
    if (tab === "test-data") {
      loadTestdata();
    }
    if (tab === "automations") {
      loadSchedules();
      loadWebhooks();
      loadFlaky();
      loadVisualChecks();
    }
    if (tab === "requirements") {
      loadRequirements();
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

  // ---------- History & comparison (Phase 13) ----------

  async function loadHistory() {
    try {
      setHistory(await api.get<HistoryRow[]>(`/projects/${id}/history/runs`));
    } catch {
      setHistory([]);
    }
  }

  async function runCompare() {
    if (!compareBase || !compareTarget || compareBase === compareTarget) return;
    setCompareBusy(true);
    setError(null);
    try {
      setCompareResult(
        await api.post<CompareResult>(`/projects/${id}/history/compare`, {
          base_run_id: compareBase,
          target_run_id: compareTarget,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Comparison failed");
    } finally {
      setCompareBusy(false);
    }
  }

  // ---------- Assistant & test data (Phase 14) ----------

  async function loadTestdata() {
    try {
      const [envRes, dsRes] = await Promise.all([
        api.get<{ items: EnvRow[] }>(`/projects/${id}/environments`),
        api.get<{ items: DatasetRow[] }>(`/projects/${id}/datasets`),
      ]);
      setEnvs(envRes.items);
      setDatasets(dsRes.items);
    } catch {
      setEnvs([]);
      setDatasets([]);
    }
  }

  async function previewResolved() {
    setError(null);
    try {
      const qs = dsForm.envId ? `?environment_id=${dsForm.envId}` : "";
      setResolvedPreview(await api.get(`/projects/${id}/test-data/resolve${qs}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Resolve failed");
    }
  }

  async function saveEnv() {
    setError(null);
    let vars: Record<string, string> = {};
    try {
      vars = envForm.varsText.trim() ? JSON.parse(envForm.varsText) : {};
    } catch {
      setError("Variables must be valid JSON, e.g. {\"user\": \"alice\"}");
      return;
    }
    try {
      await api.post(`/projects/${id}/environments`, {
        name: envForm.name,
        base_url: envForm.base_url,
        variables: vars,
      });
      setEnvForm({ name: "", base_url: "", varsText: "" });
      await loadTestdata();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save environment failed");
    }
  }

  async function saveDataset() {
    setError(null);
    let values: Record<string, string> = {};
    try {
      values = dsForm.valuesText.trim() ? JSON.parse(dsForm.valuesText) : {};
    } catch {
      setError("Values must be valid JSON, e.g. {\"password\": \"s3cret\"}");
      return;
    }
    try {
      await api.post(`/projects/${id}/datasets`, {
        name: dsForm.name,
        kind: dsForm.kind,
        generator: dsForm.kind === "generated" ? dsForm.generator : "",
        values,
        environment_id: dsForm.envId || null,
      });
      setDsForm({ name: "", kind: "static", generator: "", valuesText: "", envId: "" });
      await loadTestdata();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save dataset failed");
    }
  }

  async function deleteEnv(envId: string) {
    setError(null);
    try {
      await api.del(`/projects/${id}/environments/${envId}`);
      await loadTestdata();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed (datasets linked?)");
    }
  }

  async function deleteDataset(dsId: string) {
    setError(null);
    try {
      await api.del(`/projects/${id}/datasets/${dsId}`);
      await loadTestdata();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function toggleDataset(ds: DatasetRow) {
    setError(null);
    try {
      await api.patch(`/projects/${id}/datasets/${ds.id}`, {
        name: ds.name,
        kind: ds.kind,
        generator: ds.generator,
        generator_params: ds.generator_params,
        values: ds.values,
        environment_id: ds.environment_id,
        is_active: !ds.is_active,
      });
      await loadTestdata();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Toggle failed");
    }
  }

  async function askAssistant() {
    const question = chatInput.trim();
    if (!question || chatBusy) return;
    setChatBusy(true);
    setChatInput("");
    setChat((c) => [...c, { role: "user", text: question }]);
    try {
      const res = await api.post<{ answer: string; intent: string; grounded: boolean }>(
        `/projects/${id}/assistant/ask`,
        { question },
      );
      setChat((c) => [...c, { role: "assistant", text: res.answer, intent: res.intent, grounded: res.grounded }]);
    } catch (err) {
      setChat((c) => [
        ...c,
        { role: "assistant", text: err instanceof Error ? err.message : "Assistant unavailable" },
      ]);
    } finally {
      setChatBusy(false);
    }
  }

  // ---------- Requirements & traceability (PLAN V2.1) ----------

  async function loadRequirements() {
    try {
      setRequirements(await api.get<RequirementRow[]>(`/projects/${id}/requirements`));
    } catch {
      setRequirements([]);
    }
  }

  async function loadTrace() {
    setReqMsg(null);
    try {
      setTrace(await api.get(`/projects/${id}/requirements/traceability`));
    } catch (err) {
      setReqMsg(err instanceof Error ? err.message : "Traceability load failed");
    }
  }

  async function importRequirementText() {
    if (!reqText.trim()) return;
    setReqBusy(true);
    setReqMsg(null);
    try {
      const res = await api.post<{ imported: number; skipped: number; skipped_ids: string[] }>(
        `/projects/${id}/requirements/import`,
        { text: reqText, source_name: "pasted" },
      );
      setReqText("");
      setReqMsg(`Imported ${res.imported} requirements${res.skipped ? ` — skipped ${res.skipped} duplicates` : ""}.`);
      await loadRequirements();
    } catch (err) {
      setReqMsg(err instanceof Error ? err.message : "Import failed");
    } finally {
      setReqBusy(false);
    }
  }

  async function importRequirementFile(file: File) {
    setReqBusy(true);
    setReqMsg(null);
    try {
      const res = await api.upload<{ imported: number; skipped: number }>(
        `/projects/${id}/requirements/import-file`,
        file,
      );
      setReqMsg(`Imported ${res.imported} requirements from ${file.name}.`);
      await loadRequirements();
    } catch (err) {
      setReqMsg(err instanceof Error ? err.message : "Import failed");
    } finally {
      setReqBusy(false);
    }
  }

  async function generateForRequirements(reqIds: string[]) {
    setReqBusy(true);
    setReqMsg(null);
    try {
      const res = await api.post<{ generated: number; skipped: number; per_requirement: Record<string, { created?: number; skipped?: number; refs?: string[]; error?: string }> }>(
        `/projects/${id}/requirements/generate-cases`,
        { requirement_ids: reqIds, use_llm: false },
      );
      const errs = Object.entries(res.per_requirement).filter(([, v]) => v.error);
      setReqMsg(
        `Generated ${res.generated} test cases${res.skipped ? ` (${res.skipped} already existed)` : ""}` +
          (errs.length ? ` — errors: ${errs.map(([k]) => k).join(", ")}` : ""),
      );
      await loadRequirements();
    } catch (err) {
      setReqMsg(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setReqBusy(false);
    }
  }

  async function deleteRequirement(reqId: string) {
    setReqMsg(null);
    try {
      await api.del(`/projects/${id}/requirements/${reqId}`);
      await loadRequirements();
      setTrace(null);
    } catch (err) {
      setReqMsg(err instanceof Error ? err.message : "Delete failed");
    }
  }

  // ---------- Automations (Tier-1) ----------

  async function loadSchedules() {
    try {
      setSchedules((await api.get<{ items: ScheduleRow[] }>(`/projects/${id}/schedules`)).items);
    } catch {
      setSchedules([]);
    }
  }

  async function loadWebhooks() {
    try {
      setWebhooks((await api.get<{ items: WebhookRow[] }>(`/projects/${id}/webhooks`)).items);
    } catch {
      setWebhooks([]);
    }
  }

  async function loadFlaky() {
    try {
      setFlaky((await api.get<{ items: FlakyRow[] }>(`/projects/${id}/flaky`)).items);
    } catch {
      setFlaky([]);
    }
  }

  async function loadVisualChecks() {
    try {
      setVisualChecks((await api.get<{ items: VisualCheckRow[] }>(`/projects/${id}/visual/checks`)).items);
    } catch {
      setVisualChecks([]);
    }
  }

  async function createSchedule() {
    setError(null);
    try {
      await api.post(`/projects/${id}/schedules`, {
        name: schedForm.name,
        cron: schedForm.cron,
        browsers: schedForm.browsers.split(",").map((b) => b.trim()).filter(Boolean),
        parallelism: Number(schedForm.parallelism),
      });
      setSchedForm({ name: "", cron: "0 2 * * *", browsers: "chromium", parallelism: 2 });
      await loadSchedules();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create schedule failed");
    }
  }

  async function toggleSchedule(s: ScheduleRow) {
    setError(null);
    try {
      await api.patch(`/projects/${id}/schedules/${s.id}`, {
        name: s.name,
        cron: s.cron,
        browsers: s.browsers,
        parallelism: s.parallelism,
        is_active: !s.is_active,
      });
      await loadSchedules();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Toggle failed");
    }
  }

  async function triggerSchedule(s: ScheduleRow) {
    setError(null);
    try {
      await api.post(`/projects/${id}/schedules/${s.id}/trigger`);
      await loadSchedules();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Trigger failed");
    }
  }

  async function deleteSchedule(s: ScheduleRow) {
    setError(null);
    try {
      await api.del(`/projects/${id}/schedules/${s.id}`);
      await loadSchedules();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function createWebhook() {
    setError(null);
    try {
      const res = await api.post<{ secret?: string }>(`/projects/${id}/webhooks`, {
        name: hookForm.name,
        url: hookForm.url,
        events: [],
      });
      setHookForm({ name: "", url: "" });
      setRecorderMsg(
        res.secret
          ? `Webhook created. Signing secret (shown once): ${res.secret}`
          : "Webhook created.",
      );
      await loadWebhooks();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create webhook failed");
    }
  }

  async function testWebhook(w: WebhookRow) {
    setError(null);
    try {
      const res = await api.post<{ ok: boolean; status?: string }>(`/projects/${id}/webhooks/${w.id}/test`);
      setRecorderMsg(res.ok ? "Test delivery OK ✓" : `Delivery failed: ${res.status ?? "error"}`);
      await loadWebhooks();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Test failed");
    }
  }

  async function deleteWebhook(w: WebhookRow) {
    setError(null);
    try {
      await api.del(`/projects/${id}/webhooks/${w.id}`);
      await loadWebhooks();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function importRecording() {
    setError(null);
    setRecorderMsg("");
    const lines = recorderText
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith("#") && !l.startsWith("//"));
    if (!lines.length) {
      setError("Paste Playwright codegen output first");
      return;
    }
    try {
      const res = await api.post<{ ref: string; steps: unknown[]; warnings: string[] }>(
        `/projects/${id}/recorder/import`,
        { name: `Recorded ${new Date().toLocaleString()}`, actions: lines.map((line) => ({ line })), approve: false },
      );
      setRecorderMsg(
        `Imported as ${res.ref} with ${res.steps.length} steps${res.warnings.length ? ` — ${res.warnings.length} warning(s)` : ""}. Review it in the Test Cases tab.`,
      );
      setRecorderText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    }
  }

  async function approveVisualCheck(checkId: string) {
    setError(null);
    try {
      await api.post(`/projects/${id}/visual/checks/${checkId}/approve`);
      await loadVisualChecks();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approve failed");
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
      <div className="p-8 text-slate-500">{error ?? "Loading…"}</div>
    );
  }

  return (
    <div className="p-8 max-w-6xl">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3">
            <Link to="/" className="text-slate-500 hover:text-slate-700 text-sm">
              ← Projects
            </Link>
          </div>
          <h1 className="mt-2 text-2xl font-bold">{project.name}</h1>
          <p className="text-sm text-slate-500">{project.base_url}</p>
        </div>
        <span
          className={`rounded-full px-3 py-1 text-xs ${
            project.authorization_confirmed
              ? "bg-emerald-50 text-emerald-600"
              : "bg-amber-50 text-amber-700"
          }`}
        >
          {project.authorization_confirmed ? "authorized" : "authorization unconfirmed"}
        </span>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-600">
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="mb-6 flex gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2.5 text-sm capitalize transition-colors ${
              tab === t
                ? "border-b-2 border-brand-500 text-brand-600 font-medium"
                : "text-slate-500 hover:text-slate-800"
            }`}
          >
            {t.replace("-", " ")}
          </button>
        ))}
      </div>

      {/* ---------- Overview ---------- */}
      {tab === "overview" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
            <h3 className="font-semibold mb-2">Description</h3>
            <p className="text-sm text-slate-500">
              {project.description || "No description provided."}
            </p>
          </div>

          {/* ---- Test credentials & sign-in (authenticated targets) ---- */}
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
            <div className="flex items-center justify-between mb-1">
              <h3 className="font-semibold">Test credentials &amp; sign-in</h3>
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs ${
                  project.has_credentials
                    ? "bg-emerald-50 text-emerald-600"
                    : "bg-slate-100 text-slate-500"
                }`}
              >
                {project.has_credentials ? "configured" : "not configured"}
              </span>
            </div>
            <p className="text-sm text-slate-500 mb-4">
              Used to sign in before scans/tests and to authenticate API calls. Stored
              encrypted (Fernet), shown masked, never included in logs or reports.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Username / email</label>
                <input
                  value={credForm.username}
                  onChange={(e) => setCredForm({ ...credForm, username: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                  placeholder={(project.credentials?.username as string) || "qa-bot@yourapp.com"}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Password</label>
                <input
                  type="password"
                  value={credForm.password}
                  onChange={(e) => setCredForm({ ...credForm, password: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                  placeholder={project.credentials?.password ? "•••••••• (saved)" : "password"}
                />
              </div>
              <div className="md:col-span-2">
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Login page URL <span className="text-slate-400">(browser sign-in before scans/tests)</span>
                </label>
                <input
                  value={credForm.login_url}
                  onChange={(e) => setCredForm({ ...credForm, login_url: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                  placeholder="https://staging.example.com/login"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Username field selector</label>
                <input
                  value={credForm.username_selector}
                  onChange={(e) => setCredForm({ ...credForm, username_selector: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Password field selector</label>
                <input
                  value={credForm.password_selector}
                  onChange={(e) => setCredForm({ ...credForm, password_selector: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Submit selector</label>
                <input
                  value={credForm.submit_selector}
                  onChange={(e) => setCredForm({ ...credForm, submit_selector: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Success check <span className="text-slate-400">(url_not_contains|url_contains|text_present|selector_present: value)</span>
                </label>
                <input
                  value={credForm.success_assert}
                  onChange={(e) => setCredForm({ ...credForm, success_assert: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                  placeholder="url_not_contains:login"
                />
              </div>
              <div className="md:col-span-2">
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  API auth header <span className="text-slate-400">(attached to every API test request)</span>
                </label>
                <div className="flex gap-2">
                  <input
                    value={credForm.api_auth_header}
                    onChange={(e) => setCredForm({ ...credForm, api_auth_header: e.target.value })}
                    className="w-48 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                    placeholder="Authorization"
                  />
                  <input
                    value={credForm.api_auth_prefix}
                    onChange={(e) => setCredForm({ ...credForm, api_auth_prefix: e.target.value })}
                    className="w-28 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                    placeholder="Bearer "
                  />
                  <input
                    value={credForm.api_auth_header_value}
                    onChange={(e) => setCredForm({ ...credForm, api_auth_header_value: e.target.value })}
                    className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                    placeholder={project.credentials?.api_token ? "•••••••• (saved)" : "paste a static token, or leave empty to fetch via token endpoint"}
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Token endpoint <span className="text-slate-400">(POST, JSON body; returns the token)</span>
                </label>
                <input
                  value={credForm.token_endpoint}
                  onChange={(e) => setCredForm({ ...credForm, token_endpoint: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                  placeholder="/api/auth/login"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Token JSON path</label>
                <input
                  value={credForm.token_path}
                  onChange={(e) => setCredForm({ ...credForm, token_path: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                  placeholder="token"
                />
              </div>
            </div>
            {credMsg && <p className="mt-3 text-sm text-slate-600">{credMsg}</p>}
            <div className="mt-4 flex justify-end gap-3">
              {project.has_credentials && (
                <button
                  onClick={() => saveCredentials(true)}
                  disabled={credBusy}
                  className="rounded-lg px-4 py-2 text-sm text-red-600 hover:text-red-500 disabled:opacity-50"
                >
                  Clear credentials
                </button>
              )}
              <button
                onClick={() => saveCredentials(false)}
                disabled={credBusy || (!credForm.username && !credForm.password && !credForm.api_auth_header_value && !credForm.login_url)}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-5 py-2 text-sm font-semibold"
              >
                {credBusy ? "Saving…" : "Save credentials"}
              </button>
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
            <h3 className="font-semibold mb-3">Pipeline</h3>
            <ol className="space-y-2 text-sm text-slate-500">
              <li>1. Discover — scan the site (Discovery tab)</li>
              <li>2. Analyze — frontend stack + API inventory (APIs tab)</li>
              <li>3. Plan — generate the test plan (Test Plan tab)</li>
              <li>4. Generate &amp; review test cases (Test Cases tab)</li>
              <li>5. Execute — coming in Phase 6</li>
            </ol>
          </div>
        </div>
      )}

      {/* ---------- Requirements & Traceability (PLAN V2.1) ---------- */}
      {tab === "requirements" && (
        <div className="space-y-4">
          {/* Import card */}
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
            <h3 className="font-semibold">Import requirements</h3>
            <p className="text-sm text-slate-500 mt-0.5">
              Paste text (blank-line or heading separated, <code>REQ-xxx:</code> ids optional) or upload
              .md / .txt / .pdf / .docx / .xlsx / .csv / .json (Jira export). Generated cases enter the
              normal review gate in Test Cases.
            </p>
            <textarea
              value={reqText}
              onChange={(e) => setReqText(e.target.value)}
              rows={5}
              placeholder={"REQ-AUTH-001: Password reset via email\nUser can reset password using the registered email.\n\nREQ-AUTH-002: Dashboard shows order summary\n…"}
              className="mt-3 w-full rounded-lg border border-slate-300 bg-white px-3.5 py-2.5 text-sm font-mono focus:border-brand-500 focus:outline-none"
            />
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button
                onClick={importRequirementText}
                disabled={reqBusy || !reqText.trim()}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm font-semibold text-white"
              >
                {reqBusy ? "Working…" : "Import text"}
              </button>
              <label className="cursor-pointer rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
                Upload file…
                <input
                  type="file"
                  accept=".md,.markdown,.txt,.pdf,.docx,.xlsx,.csv,.json"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) importRequirementFile(f);
                    e.target.value = "";
                  }}
                />
              </label>
              {reqMsg && <span className="text-sm text-slate-600">{reqMsg}</span>}
            </div>
          </div>

          {/* Requirements table */}
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
              <h3 className="font-semibold">Requirements ({requirements.length})</h3>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => generateForRequirements([...selectedReqs])}
                  disabled={reqBusy || selectedReqs.size === 0}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  Generate for selected ({selectedReqs.size})
                </button>
                <button
                  onClick={() => generateForRequirements(requirements.map((r) => r.id))}
                  disabled={reqBusy || requirements.length === 0}
                  className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm font-semibold text-white"
                >
                  Generate cases for all
                </button>
              </div>
            </div>
            {requirements.length === 0 ? (
              <div className="p-10 text-center text-sm text-slate-500">
                No requirements yet — paste a BRD excerpt above or upload a document.
              </div>
            ) : (
              <div className="divide-y divide-slate-100">
                {requirements.map((r) => (
                  <div key={r.id} className="flex items-center gap-4 px-6 py-3.5">
                    <input
                      type="checkbox"
                      checked={selectedReqs.has(r.id)}
                      onChange={(e) => {
                        const next = new Set(selectedReqs);
                        if (e.target.checked) next.add(r.id);
                        else next.delete(r.id);
                        setSelectedReqs(next);
                      }}
                      className="h-4 w-4"
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-slate-800 truncate">
                        <span className="font-mono text-xs text-brand-600 mr-2">{r.external_id}</span>
                        {r.title}
                      </p>
                      <p className="text-xs text-slate-500 truncate">{r.description || "—"}</p>
                    </div>
                    <span className={`rounded-full px-2.5 py-0.5 text-xs capitalize ${
                      r.priority === "critical" ? "bg-red-50 text-red-600"
                      : r.priority === "high" ? "bg-orange-500/10 text-orange-400"
                      : r.priority === "medium" ? "bg-yellow-500/10 text-yellow-400"
                      : "bg-slate-100 text-slate-500"}`}>
                      {r.priority}
                    </span>
                    <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-500">{r.status}</span>
                    {r.coverage_count ? (
                      <span className="text-xs text-emerald-600 whitespace-nowrap">✓ {r.coverage_count} cases</span>
                    ) : (
                      <span className="text-xs text-amber-600 whitespace-nowrap">no tests</span>
                    )}
                    <button
                      onClick={() => generateForRequirements([r.id])}
                      disabled={reqBusy}
                      className="text-sm text-brand-600 hover:text-brand-600 disabled:opacity-50"
                    >
                      Generate
                    </button>
                    <button
                      onClick={() => deleteRequirement(r.id)}
                      className="text-sm text-slate-400 hover:text-red-500"
                    >
                      Delete
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Traceability matrix */}
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
              <div>
                <h3 className="font-semibold">Traceability matrix</h3>
                <p className="text-sm text-slate-500 mt-0.5">Requirement → test cases → latest result → defects</p>
              </div>
              <button
                onClick={loadTrace}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
              >
                {trace ? "Refresh" : "Load matrix"}
              </button>
            </div>
            {trace && (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 px-6 py-4">
                  {["Requirements", "Covered", "Uncovered", "Pass rate"].map((label, i) => (
                    <div key={label} className="rounded-lg bg-slate-50 px-4 py-3">
                      <p className="text-xs text-slate-500">{label}</p>
                      <p className="text-lg font-semibold text-slate-800">
                        {i === 0 ? trace.summary.requirements
                          : i === 1 ? trace.summary.covered
                          : i === 2 ? trace.summary.uncovered
                          : trace.summary.pass_rate === null ? "—"
                          : `${Math.round(trace.summary.pass_rate * 100)}%`}
                      </p>
                    </div>
                  ))}
                </div>
                <div className="divide-y divide-slate-100">
                  {trace.rows.map((row) => (
                    <div key={row.external_id} className="px-6 py-3.5">
                      <div className="flex items-center gap-3">
                        <span className="font-mono text-xs text-brand-600">{row.external_id}</span>
                        <span className="text-sm text-slate-700 truncate flex-1">{row.title}</span>
                        {row.defect_count > 0 && (
                          <span className="rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600">
                            {row.defect_count} defect{row.defect_count > 1 ? "s" : ""}
                          </span>
                        )}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {row.cases.map((c, idx) => {
                          const res = row.results[idx];
                          return (
                            <span
                              key={c.ref}
                              title={`${c.scenario} — ${res}`}
                              className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
                                res === "passed" ? "bg-emerald-50 text-emerald-600"
                                : res === "failed" ? "bg-red-50 text-red-600"
                                : res === "blocked" ? "bg-amber-50 text-amber-600"
                                : "bg-slate-100 text-slate-500"}`}
                            >
                              {c.ref.replace(/^TC-/, "")}{c.approved ? "" : " ·"}
                            </span>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* ---------- Discovery ---------- */}
      {tab === "discovery" && (
        <div className="space-y-4">
          {!project.authorization_confirmed ? (
            <div className="rounded-xl border border-amber-300 bg-amber-50 p-6 text-sm text-amber-900">
              Authorization must be confirmed before scanning. Confirm you are authorized to
              test this application:
              <button
                onClick={confirmAuthorization}
                className="ml-3 rounded-lg bg-amber-600 hover:bg-amber-500 px-4 py-1.5 text-sm font-semibold text-white"
              >
                I confirm authorization
              </button>
            </div>
          ) : (
            <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
              <div className="flex items-end gap-4">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">
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
                    className="w-24 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">
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
                    className="w-24 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
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
                <p className="mt-3 text-sm text-slate-500">
                  {scanState}
                  {scanState.includes("running") && (
                    <span className="ml-2 inline-block h-2 w-2 animate-pulse rounded-full bg-brand-500" />
                  )}
                </p>
              )}
            </div>
          )}

          <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
              <h3 className="font-semibold">Discovered pages ({pages.length})</h3>
              <button onClick={runAnalysis} className="text-sm text-brand-600 hover:text-brand-600">
                Run architecture analysis →
              </button>
            </div>
            {pages.length === 0 ? (
              <p className="px-6 py-8 text-sm text-slate-500">No pages discovered yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                    <th className="px-6 py-3">URL</th>
                    <th className="px-6 py-3">Title</th>
                    <th className="px-6 py-3">Depth</th>
                    <th className="px-6 py-3">Status</th>
                    <th className="px-6 py-3">Components</th>
                  </tr>
                </thead>
                <tbody>
                  {pages.map((p) => (
                    <tr key={p.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-6 py-3 text-slate-800 font-mono text-xs">{p.url}</td>
                      <td className="px-6 py-3 text-slate-500">{p.title || "—"}</td>
                      <td className="px-6 py-3 text-slate-500">{p.depth}</td>
                      <td className="px-6 py-3">
                        <span
                          className={`rounded px-1.5 py-0.5 text-xs ${
                            p.status_code === 200
                              ? "bg-emerald-50 text-emerald-600"
                              : "bg-slate-100 text-slate-500"
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
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
            <h3 className="font-semibold">API inventory ({apis.length})</h3>
            <button onClick={runAnalysis} className="text-sm text-brand-600 hover:text-brand-600">
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
                <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                  <th className="px-6 py-3">Method</th>
                  <th className="px-6 py-3">Path</th>
                  <th className="px-6 py-3">Group</th>
                  <th className="px-6 py-3">Source</th>
                </tr>
              </thead>
              <tbody>
                {apis.map((a) => (
                  <tr key={a.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-6 py-3">
                      <span className="rounded bg-brand-50 px-1.5 py-0.5 text-xs font-mono text-brand-600">
                        {a.method}
                      </span>
                    </td>
                    <td className="px-6 py-3 font-mono text-xs text-slate-800">{a.path}</td>
                    <td className="px-6 py-3 text-slate-500 capitalize">{a.group}</td>
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
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6 flex items-center justify-between">
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
                      ? "bg-slate-100 text-slate-700"
                      : "bg-emerald-600 hover:bg-emerald-500"
                  }`}
                >
                  {plan.approved ? "Revoke approval" : "Approve"}
                </button>
              )}
            </div>
          </div>

          {!plan ? (
            <div className="rounded-xl border border-dashed border-slate-200 p-12 text-center text-slate-500">
              No plan yet — generate one from discovery evidence.
            </div>
          ) : (
            <>
              <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-1">
                  Objective
                </h4>
                <p className="text-sm text-slate-700">{plan.objective}</p>
              </div>
              <div className="space-y-2">
                {plan.sections.map((s, i) => (
                  <div
                    key={i}
                    className="rounded-xl border border-slate-200 bg-white shadow-sm px-5 py-4 flex items-start gap-4"
                  >
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase font-semibold ${
                        PRIORITY_STYLES[s.priority] ?? PRIORITY_STYLES.low
                      }`}
                    >
                      {s.priority}
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-slate-800">{s.section}</p>
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
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6 flex items-center justify-between">
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
            <div className="rounded-xl border border-dashed border-slate-200 p-12 text-center text-slate-500">
              No runs yet — approve test cases, then start a run.
            </div>
          ) : (
            <div className="space-y-2">
              {runs.map((r) => (
                <div key={r.id} className="rounded-xl border border-slate-200 bg-white shadow-sm px-5 py-4 flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-slate-800">{r.label}</p>
                    <p className="text-xs text-slate-500">
                      {new Date(r.created_at).toLocaleString()} · browsers: {(r.browsers ?? []).join(", ")}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className={`rounded-full px-2.5 py-0.5 text-xs capitalize ${
                      r.status === "completed" ? "bg-emerald-50 text-emerald-600"
                      : r.status === "running" ? "bg-brand-50 text-brand-600"
                      : "bg-slate-100 text-slate-500"}`}>
                      {r.status}
                    </span>
                    <Link
                      to={`/projects/${id}/runs/${r.id}`}
                      className="text-sm text-brand-600 hover:text-brand-600"
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
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6 flex items-center justify-between">
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
                  className="rounded-lg bg-slate-100 hover:bg-slate-200 px-4 py-2 text-sm font-semibold"
                >
                  Approve all
                </button>
              )}
            </div>
          </div>

          {cases.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 p-12 text-center text-slate-500">
              No test cases yet — generate them from discovery data.
            </div>
          ) : (
            <div className="space-y-2">
              {cases.map((c) => (
                <div
                  key={c.id}
                  className="rounded-xl border border-slate-200 bg-white shadow-sm px-5 py-4"
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
                        <span className="font-mono text-xs text-brand-600">{c.ref}</span>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] uppercase font-semibold ${
                            PRIORITY_STYLES[c.priority] ?? PRIORITY_STYLES.low
                          }`}
                        >
                          {c.priority}
                        </span>
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase text-slate-500">
                          {c.kind}
                        </span>
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                          {c.module}
                        </span>
                        {c.approved && (
                          <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-600">
                            approved
                          </span>
                        )}
                        {!c.enabled && (
                          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                            disabled
                          </span>
                        )}
                      </div>
                      <p className="mt-1.5 text-sm text-slate-800">{c.scenario}</p>
                      <p className="mt-1 text-xs text-slate-500">
                        <span className="text-slate-600">Expected:</span> {c.expected_result}
                      </p>
                      <div className="mt-2 flex gap-2">
                        {!c.approved && (
                          <button
                            onClick={() => reviewCase(c.ref, "approve")}
                            className="rounded bg-emerald-50 px-2.5 py-1 text-xs text-emerald-600 hover:bg-emerald-600/30"
                          >
                            Approve
                          </button>
                        )}
                        <button
                          onClick={() => {
                            const next = window.prompt("Edit expected result", c.expected_result);
                            if (next !== null) reviewCase(c.ref, "approve", { expected_result: next });
                          }}
                          className="rounded bg-slate-100 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-200"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => reviewCase(c.ref, "duplicate")}
                          className="rounded bg-slate-100 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-200"
                        >
                          Duplicate
                        </button>
                        <button
                          onClick={() => reviewCase(c.ref, c.enabled ? "disable" : "enable")}
                          className="rounded bg-slate-100 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-200"
                        >
                          {c.enabled ? "Disable" : "Enable"}
                        </button>
                        <button
                          onClick={() => reviewCase(c.ref, "reject")}
                          className="rounded bg-red-50 px-2.5 py-1 text-xs text-red-600 hover:bg-red-100"
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
            <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
              Authorization must be confirmed before running accessibility or performance modules.
            </div>
          )}

          {/* Accessibility */}
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
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
              <p className="px-6 py-3 text-sm text-slate-500">
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
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                    <th className="px-6 py-3">Page</th>
                    <th className="px-6 py-3">Violations</th>
                    <th className="px-6 py-3">By severity</th>
                    <th className="px-6 py-3">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {a11y.map((r) => (
                    <tr key={r.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-6 py-3 font-mono text-xs text-slate-800 max-w-72 truncate">{r.page_url}</td>
                      <td className="px-6 py-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                          r.violation_count === 0 ? "bg-emerald-50 text-emerald-600"
                          : "bg-red-50 text-red-600"}`}>
                          {r.violation_count}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-xs text-slate-500">
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
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="px-6 py-4 border-b border-slate-200">
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
                  <label key={key} className="text-xs text-slate-500">
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
                      className="w-24 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm"
                    />
                  </label>
                ))}
              </div>
              {perfState && <p className="mt-3 text-sm text-slate-500">{perfState}</p>}
            </div>
            {perf.length === 0 ? (
              <p className="px-6 py-8 text-sm text-slate-500">No performance results yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
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
                    <tr key={r.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-6 py-3 text-xs text-slate-500">{new Date(r.created_at).toLocaleString()}</td>
                      <td className="px-6 py-3 font-mono text-xs text-slate-800">{r.scenario}</td>
                      <td className="px-6 py-3 text-slate-500">{r.meta?.total_requests ?? "—"}</td>
                      <td className="px-6 py-3 text-slate-700">{r.p50_ms} / {r.p95_ms} / {r.p99_ms}</td>
                      <td className="px-6 py-3 text-slate-500">{r.throughput_rps}</td>
                      <td className="px-6 py-3 text-slate-500">{(r.error_rate * 100).toFixed(1)}%</td>
                      <td className="px-6 py-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                          r.meta?.threshold_breached ? "bg-red-50 text-red-600" : "bg-emerald-50 text-emerald-600"}`}>
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
      {/* ---------- History & comparison (Phase 13, §21) ---------- */}
      {tab === "history" && (
        <div className="space-y-4">
          {/* Comparison picker */}
          <div className="card p-6">
            <h3 className="font-semibold">Compare runs</h3>
            <p className="text-sm text-slate-500 mt-0.5">
              Diff two executions: new, resolved, and persistent failures, test-set changes, performance deltas
            </p>
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Base (older)</span>
                <select
                  value={compareBase}
                  onChange={(e) => setCompareBase(e.target.value)}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm min-w-56"
                >
                  <option value="">Select run…</option>
                  {history.map((h) => (
                    <option key={h.id} value={h.id}>{h.label}</option>
                  ))}
                </select>
              </label>
              <span className="pb-2.5 text-slate-400">→</span>
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Target (newer)</span>
                <select
                  value={compareTarget}
                  onChange={(e) => setCompareTarget(e.target.value)}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm min-w-56"
                >
                  <option value="">Select run…</option>
                  {history.map((h) => (
                    <option key={h.id} value={h.id}>{h.label}</option>
                  ))}
                </select>
              </label>
              <button
                onClick={runCompare}
                disabled={!compareBase || !compareTarget || compareBase === compareTarget || compareBusy}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-5 py-2 text-sm font-semibold text-white"
              >
                {compareBusy ? "Comparing…" : "Compare"}
              </button>
            </div>
          </div>

          {compareResult && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  ["New failures", compareResult.new_failures.length, "text-red-600"],
                  ["Resolved", compareResult.resolved_failures.length, "text-emerald-600"],
                  ["Persistent", compareResult.persistent_failures.length, "text-amber-600"],
                  ["Test set changed", compareResult.new_tests.length + compareResult.removed_tests.length, "text-slate-700"],
                ].map(([label, value, cls]) => (
                  <div key={label as string} className="card px-4 py-3">
                    <p className="text-xs text-slate-500">{label}</p>
                    <p className={`text-2xl font-bold ${cls}`}>{value}</p>
                  </div>
                ))}
              </div>

              <div className="grid md:grid-cols-2 gap-4">
                <div className="card p-5">
                  <h4 className="text-sm font-semibold mb-3">New failures ({compareResult.new_failures.length})</h4>
                  {compareResult.new_failures.length === 0 ? (
                    <p className="text-sm text-slate-500">None 🎉</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {compareResult.new_failures.map((f) => (
                        <li key={f.ref + f.browser} className="text-sm">
                          <span className="font-mono text-xs text-brand-600">{f.ref}</span>
                          <span className="ml-2 text-xs text-slate-500">{f.browser}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="card p-5">
                  <h4 className="text-sm font-semibold mb-3">Resolved failures ({compareResult.resolved_failures.length})</h4>
                  {compareResult.resolved_failures.length === 0 ? (
                    <p className="text-sm text-slate-500">None</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {compareResult.resolved_failures.map((f) => (
                        <li key={f.ref + f.browser} className="text-sm">
                          <span className="font-mono text-xs text-brand-600">{f.ref}</span>
                          <span className="ml-2 text-xs text-slate-500">{f.browser}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="card p-5">
                  <h4 className="text-sm font-semibold mb-3">Persistent failures ({compareResult.persistent_failures.length})</h4>
                  {compareResult.persistent_failures.length === 0 ? (
                    <p className="text-sm text-slate-500">None</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {compareResult.persistent_failures.map((f) => (
                        <li key={f.ref + f.browser} className="text-sm">
                          <span className="font-mono text-xs text-brand-600">{f.ref}</span>
                          <span className="ml-2 text-xs text-slate-500">{f.browser}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="card p-5">
                  <h4 className="text-sm font-semibold mb-3">Test set changes</h4>
                  <p className="text-xs text-slate-500 mb-2">
                    +{compareResult.new_tests.length} new · −{compareResult.removed_tests.length} removed
                  </p>
                  <ul className="space-y-1 text-sm">
                    {compareResult.new_tests.slice(0, 8).map((t) => (
                      <li key={t.ref + t.browser} className="text-emerald-600">+ {t.ref} <span className="text-xs text-slate-400">({t.browser})</span></li>
                    ))}
                    {compareResult.removed_tests.slice(0, 8).map((t) => (
                      <li key={t.ref + t.browser} className="text-slate-400">− {t.ref} <span className="text-xs text-slate-400">({t.browser})</span></li>
                    ))}
                  </ul>
                </div>
              </div>

              {compareResult.performance_changes.length > 0 && (
                <div className="card p-5">
                  <h4 className="text-sm font-semibold mb-3">Performance changes</h4>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                        <th className="py-2">Scenario</th>
                        <th className="py-2">Base p95</th>
                        <th className="py-2">Target p95</th>
                        <th className="py-2">Δ</th>
                      </tr>
                    </thead>
                    <tbody>
                      {compareResult.performance_changes.map((p) => (
                        <tr key={p.scenario} className="border-b border-slate-100 last:border-0">
                          <td className="py-2 font-mono text-xs">{p.scenario}</td>
                          <td className="py-2">{p.base_p95_ms} ms</td>
                          <td className="py-2">{p.target_p95_ms} ms</td>
                          <td className={`py-2 font-semibold ${p.delta_p95_ms > 0 ? "text-red-600" : "text-emerald-600"}`}>
                            {p.delta_p95_ms > 0 ? "+" : ""}{p.delta_p95_ms} ms
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {/* History list with pass/fail trend */}
          <div className="card">
            <div className="px-6 py-4 border-b border-slate-200">
              <h3 className="font-semibold">Execution history ({history.length})</h3>
            </div>
            {history.length === 0 ? (
              <p className="px-6 py-8 text-sm text-slate-500">No runs yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                    <th className="px-6 py-3">Run</th>
                    <th className="px-6 py-3">When</th>
                    <th className="px-6 py-3">Browsers</th>
                    <th className="px-6 py-3">Pass / Fail / Skip</th>
                    <th className="px-6 py-3">Trend</th>
                    <th className="px-6 py-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h) => {
                    const total = Math.max(h.passed + h.failed + h.skipped + h.other, 1);
                    const pct = (n: number) => (n * 100) / total;
                    return (
                      <tr key={h.id} className="border-b border-slate-100 last:border-0">
                        <td className="px-6 py-3 text-slate-800 font-medium">{h.label}</td>
                        <td className="px-6 py-3 text-xs text-slate-500">{new Date(h.created_at).toLocaleString()}</td>
                        <td className="px-6 py-3 text-xs text-slate-500">{(h.browsers ?? []).join(", ")}</td>
                        <td className="px-6 py-3 text-xs">
                          <span className="text-emerald-600 font-semibold">{h.passed}</span>
                          <span className="text-slate-400"> / </span>
                          <span className="text-red-600 font-semibold">{h.failed}</span>
                          <span className="text-slate-400"> / {h.skipped}</span>
                        </td>
                        <td className="px-6 py-3">
                          <div className="flex h-2 w-32 overflow-hidden rounded-full bg-slate-100">
                            <div className="bg-emerald-400" style={{ width: `${pct(h.passed)}%` }} />
                            <div className="bg-red-400" style={{ width: `${pct(h.failed)}%` }} />
                            <div className="bg-slate-300" style={{ width: `${pct(h.skipped + h.other)}%` }} />
                          </div>
                        </td>
                        <td className="px-6 py-3">
                          <span className={`rounded-full px-2.5 py-0.5 text-xs capitalize ${
                            h.status === "completed" ? "bg-emerald-50 text-emerald-600"
                            : h.status === "running" ? "bg-brand-50 text-brand-600"
                            : "bg-slate-100 text-slate-500"}`}>
                            {h.status}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* ---------- Assistant (Phase 14, §22) ---------- */}
      {tab === "assistant" && (
        <div className="card max-w-3xl flex flex-col" style={{ minHeight: 480 }}>
          <div className="px-6 py-4 border-b border-slate-200">
            <h3 className="font-semibold">AI QA Assistant</h3>
            <p className="text-sm text-slate-500 mt-0.5">
              Grounded in your real QA data — every number comes from live queries, never invented
            </p>
          </div>
          <div className="flex-1 overflow-y-auto p-6 space-y-3">
            {chat.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm ${
                    m.role === "user"
                      ? "bg-brand-600 text-white rounded-br-sm"
                      : "bg-slate-100 text-slate-800 rounded-bl-sm"
                  }`}
                >
                  {m.text}
                  {m.role === "assistant" && m.intent && (
                    <div className="mt-1.5 flex items-center gap-1.5">
                      <span className="rounded bg-white/70 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-500">
                        {m.intent}
                      </span>
                      {m.grounded && (
                        <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-600">
                          grounded ✓
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {chatBusy && (
              <div className="flex justify-start">
                <div className="rounded-2xl bg-slate-100 px-4 py-2.5 text-sm text-slate-400">thinking…</div>
              </div>
            )}
          </div>
          <div className="px-6 py-4 border-t border-slate-200">
            <div className="flex flex-wrap gap-2 mb-3">
              {["Give me a QA summary", "Why did tests fail?", "Show critical failures", "Compare the last two runs"].map((q) => (
                <button
                  key={q}
                  onClick={() => setChatInput(q)}
                  className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
                >
                  {q}
                </button>
              ))}
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                askAssistant();
              }}
              className="flex gap-2"
            >
              <input
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Ask about failures, defects, latency, security…"
                className="flex-1 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm focus:border-brand-500 focus:outline-none"
              />
              <button
                type="submit"
                disabled={chatBusy || !chatInput.trim()}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm font-semibold text-white"
              >
                Send
              </button>
            </form>
          </div>
        </div>
      )}

      {/* ---------- Test data (Phase 14, §17) ---------- */}
      {tab === "test-data" && (
        <div className="space-y-4">
          {/* Environments */}
          <div className="card">
            <div className="px-6 py-4 border-b border-slate-200">
              <h3 className="font-semibold">Environments ({envs.length})</h3>
              <p className="text-sm text-slate-500 mt-0.5">Named targets with per-environment variables</p>
            </div>
            {envs.length === 0 ? (
              <p className="px-6 py-6 text-sm text-slate-500">No environments yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                    <th className="px-6 py-3">Name</th>
                    <th className="px-6 py-3">Base URL</th>
                    <th className="px-6 py-3">Variables</th>
                    <th className="px-6 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {envs.map((e) => (
                    <tr key={e.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-6 py-3 font-medium text-slate-800">{e.name}</td>
                      <td className="px-6 py-3 font-mono text-xs text-slate-500 max-w-56 truncate">{e.base_url || "—"}</td>
                      <td className="px-6 py-3 font-mono text-xs text-slate-500">
                        {Object.entries(e.variables).map(([k, v]) => `${k}=${v}`).join(" · ") || "—"}
                      </td>
                      <td className="px-6 py-3 text-right">
                        <button
                          onClick={() => deleteEnv(e.id)}
                          className="rounded bg-red-50 px-2.5 py-1 text-xs text-red-600 hover:bg-red-100"
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="px-6 py-4 border-t border-slate-200 flex flex-wrap items-end gap-3">
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Name</span>
                <input
                  value={envForm.name}
                  onChange={(e) => setEnvForm({ ...envForm, name: e.target.value })}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                />
              </label>
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Base URL (optional)</span>
                <input
                  value={envForm.base_url}
                  onChange={(e) => setEnvForm({ ...envForm, base_url: e.target.value })}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm w-64"
                />
              </label>
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Variables (JSON)</span>
                <input
                  value={envForm.varsText}
                  onChange={(e) => setEnvForm({ ...envForm, varsText: e.target.value })}
                  placeholder='{"user": "alice"}'
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm w-72 font-mono"
                />
              </label>
              <button
                onClick={saveEnv}
                disabled={!envForm.name.trim()}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm font-semibold text-white"
              >
                Add environment
              </button>
            </div>
          </div>

          {/* Datasets */}
          <div className="card">
            <div className="px-6 py-4 border-b border-slate-200">
              <h3 className="font-semibold">Datasets ({datasets.length})</h3>
              <p className="text-sm text-slate-500 mt-0.5">
                Static values (secrets encrypted at rest, masked in the UI) or generated data — active datasets are merged into every run
              </p>
            </div>
            {datasets.length === 0 ? (
              <p className="px-6 py-6 text-sm text-slate-500">No datasets yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                    <th className="px-6 py-3">Name</th>
                    <th className="px-6 py-3">Kind</th>
                    <th className="px-6 py-3">Environment</th>
                    <th className="px-6 py-3">Values</th>
                    <th className="px-6 py-3">Active</th>
                    <th className="px-6 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {datasets.map((d) => (
                    <tr key={d.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-6 py-3 font-medium text-slate-800">{d.name}</td>
                      <td className="px-6 py-3">
                        <span className="rounded bg-brand-50 px-1.5 py-0.5 text-xs text-brand-600">{d.kind}</span>
                        {d.kind === "generated" && d.generator && (
                          <span className="ml-1.5 text-xs text-slate-500">({d.generator})</span>
                        )}
                      </td>
                      <td className="px-6 py-3 text-xs text-slate-500">
                        {d.environment_id ? envs.find((e) => e.id === d.environment_id)?.name ?? "—" : "all"}
                      </td>
                      <td className="px-6 py-3 font-mono text-xs text-slate-500 max-w-72 truncate">
                        {Object.entries(d.values).map(([k, v]) => `${k}=${v}`).join(" · ") || "—"}
                      </td>
                      <td className="px-6 py-3">
                        <button
                          onClick={() => toggleDataset(d)}
                          className={`rounded px-2 py-0.5 text-xs font-semibold ${
                            d.is_active ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-500"
                          }`}
                        >
                          {d.is_active ? "active" : "inactive"}
                        </button>
                      </td>
                      <td className="px-6 py-3 text-right">
                        <button
                          onClick={() => deleteDataset(d.id)}
                          className="rounded bg-red-50 px-2.5 py-1 text-xs text-red-600 hover:bg-red-100"
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="px-6 py-4 border-t border-slate-200 flex flex-wrap items-end gap-3">
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Name</span>
                <input
                  value={dsForm.name}
                  onChange={(e) => setDsForm({ ...dsForm, name: e.target.value })}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                />
              </label>
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Kind</span>
                <select
                  value={dsForm.kind}
                  onChange={(e) => setDsForm({ ...dsForm, kind: e.target.value })}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                >
                  <option value="static">static (encrypted)</option>
                  <option value="generated">generated</option>
                </select>
              </label>
              {dsForm.kind === "generated" && (
                <label className="text-xs text-slate-500">
                  <span className="block mb-1">Generator</span>
                  <select
                    value={dsForm.generator}
                    onChange={(e) => setDsForm({ ...dsForm, generator: e.target.value })}
                    className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                  >
                    {["user", "email", "string", "uuid", "int"].map((g) => (
                      <option key={g} value={g}>{g}</option>
                    ))}
                  </select>
                </label>
              )}
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Environment</span>
                <select
                  value={dsForm.envId}
                  onChange={(e) => setDsForm({ ...dsForm, envId: e.target.value })}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                >
                  <option value="">All environments</option>
                  {envs.map((e) => (
                    <option key={e.id} value={e.id}>{e.name}</option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Values (JSON)</span>
                <input
                  value={dsForm.valuesText}
                  onChange={(e) => setDsForm({ ...dsForm, valuesText: e.target.value })}
                  placeholder='{"password": "s3cret", "username": "qa_user"}'
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm w-80 font-mono"
                />
              </label>
              <button
                onClick={saveDataset}
                disabled={!dsForm.name.trim()}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm font-semibold text-white"
              >
                Add dataset
              </button>
            </div>
          </div>

          {/* Resolution preview */}
          <div className="card p-6">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="font-semibold">Resolution preview</h3>
                <p className="text-sm text-slate-500 mt-0.5">The exact variables executors receive — secrets masked</p>
              </div>
              <div className="flex items-end gap-3">
                <select
                  value={dsForm.envId}
                  onChange={(e) => setDsForm({ ...dsForm, envId: e.target.value })}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                >
                  <option value="">All environments</option>
                  {envs.map((e) => (
                    <option key={e.id} value={e.id}>{e.name}</option>
                  ))}
                </select>
                <button
                  onClick={previewResolved}
                  className="rounded-lg bg-slate-100 hover:bg-slate-200 px-4 py-2 text-sm font-semibold text-slate-700"
                >
                  Preview variables
                </button>
              </div>
            </div>
            {resolvedPreview && (
              <div className="mt-4 rounded-lg bg-slate-50 p-4">
                <p className="text-xs text-slate-500 mb-2">{resolvedPreview.count} variable(s) resolved</p>
                <div className="font-mono text-xs text-slate-700 space-y-0.5">
                  {Object.entries(resolvedPreview.variables).map(([k, v]) => (
                    <div key={k}>{k}={v}</div>
                  ))}
                </div>
              </div>
            )}
            <p className="mt-3 text-xs text-slate-400">
              Secrets never appear in logs or reports; they are decrypted only at execution time inside the worker.
            </p>
          </div>
        </div>
      )}

      {/* ---------- Automations (Tier-1) ---------- */}
      {tab === "automations" && (
        <div className="space-y-4">
          {/* Schedules */}
          <div className="card">
            <div className="px-6 py-4 border-b border-slate-200">
              <h3 className="font-semibold">Scheduled runs ({schedules.length})</h3>
              <p className="text-sm text-slate-500 mt-0.5">Cron-driven recurring regressions — dispatched automatically</p>
            </div>
            {schedules.length === 0 ? (
              <p className="px-6 py-6 text-sm text-slate-500">No schedules yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                    <th className="px-6 py-3">Name</th>
                    <th className="px-6 py-3">Cron</th>
                    <th className="px-6 py-3">Browsers</th>
                    <th className="px-6 py-3">Next run</th>
                    <th className="px-6 py-3">Last run</th>
                    <th className="px-6 py-3">Status</th>
                    <th className="px-6 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {schedules.map((s) => (
                    <tr key={s.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-6 py-3 font-medium text-slate-800">{s.name}</td>
                      <td className="px-6 py-3 font-mono text-xs text-slate-600">{s.cron}</td>
                      <td className="px-6 py-3 text-xs text-slate-500">{s.browsers.join(", ")}</td>
                      <td className="px-6 py-3 text-xs text-slate-500">{s.next_run_at ? new Date(s.next_run_at).toLocaleString() : "—"}</td>
                      <td className="px-6 py-3 text-xs text-slate-500">{s.last_run_at ? new Date(s.last_run_at).toLocaleString() : "never"}</td>
                      <td className="px-6 py-3">
                        <button
                          onClick={() => toggleSchedule(s)}
                          className={`rounded px-2 py-0.5 text-xs font-semibold ${s.is_active ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-500"}`}
                        >
                          {s.is_active ? "active" : "paused"}
                        </button>
                      </td>
                      <td className="px-6 py-3 text-right space-x-2">
                        <button onClick={() => triggerSchedule(s)} className="rounded bg-brand-50 px-2.5 py-1 text-xs text-brand-600 hover:bg-brand-500/30">Run now</button>
                        <button onClick={() => deleteSchedule(s)} className="rounded bg-red-50 px-2.5 py-1 text-xs text-red-600 hover:bg-red-100">Delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="px-6 py-4 border-t border-slate-200 flex flex-wrap items-end gap-3">
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Name</span>
                <input value={schedForm.name} onChange={(e) => setSchedForm({ ...schedForm, name: e.target.value })} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm" />
              </label>
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Cron (UTC)</span>
                <input value={schedForm.cron} onChange={(e) => setSchedForm({ ...schedForm, cron: e.target.value })} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-mono w-32" />
              </label>
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Browsers</span>
                <input value={schedForm.browsers} onChange={(e) => setSchedForm({ ...schedForm, browsers: e.target.value })} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm w-40" />
              </label>
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Parallelism</span>
                <input type="number" min={0} max={16} value={schedForm.parallelism} onChange={(e) => setSchedForm({ ...schedForm, parallelism: Number(e.target.value) })} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm w-20" />
              </label>
              <button onClick={createSchedule} disabled={!schedForm.name.trim()} className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm font-semibold text-white">Add schedule</button>
            </div>
          </div>

          {/* Flaky detection */}
          <div className="card">
            <div className="px-6 py-4 border-b border-slate-200">
              <h3 className="font-semibold">Flaky tests</h3>
              <p className="text-sm text-slate-500 mt-0.5">Instability signals from execution history: retried-pass, alternating outcomes, pass-rate drift</p>
            </div>
            {flaky.length === 0 ? (
              <p className="px-6 py-6 text-sm text-slate-500">No execution history yet — run some tests first.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                    <th className="px-6 py-3">Test case</th>
                    <th className="px-6 py-3">Runs</th>
                    <th className="px-6 py-3">Pass rate</th>
                    <th className="px-6 py-3">Signals</th>
                    <th className="px-6 py-3">Score</th>
                    <th className="px-6 py-3">Class</th>
                  </tr>
                </thead>
                <tbody>
                  {flaky.slice(0, 12).map((f) => (
                    <tr key={f.test_case_id} className="border-b border-slate-100 last:border-0">
                      <td className="px-6 py-3 font-mono text-xs text-brand-600">{f.test_case_id.slice(0, 8)}…</td>
                      <td className="px-6 py-3 text-slate-500">{f.runs_observed}</td>
                      <td className="px-6 py-3 text-slate-700">{(f.pass_rate * 100).toFixed(0)}%</td>
                      <td className="px-6 py-3 text-xs text-slate-500">
                        {[f.retried_pass && "retried-pass", f.alternating && "alternating"].filter(Boolean).join(", ") || "—"}
                      </td>
                      <td className="px-6 py-3 font-semibold">{f.flaky_score}</td>
                      <td className="px-6 py-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${f.classification === "flaky" ? "bg-red-50 text-red-600" : f.classification === "suspect" ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-600"}`}>
                          {f.classification}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Visual checks */}
          <div className="card">
            <div className="px-6 py-4 border-b border-slate-200">
              <h3 className="font-semibold">Visual checks ({visualChecks.length})</h3>
              <p className="text-sm text-slate-500 mt-0.5">Pixel-diff of passing-run screenshots vs approved baselines. Approve a changed screenshot to update the baseline.</p>
            </div>
            {visualChecks.length === 0 ? (
              <p className="px-6 py-6 text-sm text-slate-500">No visual checks yet — set a baseline (upload below) and run the case; passing runs are compared automatically.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                    <th className="px-6 py-3">When</th>
                    <th className="px-6 py-3">Case</th>
                    <th className="px-6 py-3">Browser</th>
                    <th className="px-6 py-3">Diff</th>
                    <th className="px-6 py-3">Status</th>
                    <th className="px-6 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {visualChecks.map((c) => (
                    <tr key={c.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-6 py-3 text-xs text-slate-500">{new Date(c.created_at).toLocaleString()}</td>
                      <td className="px-6 py-3 font-mono text-xs text-brand-600">{c.test_case_id.slice(0, 8)}…</td>
                      <td className="px-6 py-3 text-xs text-slate-500">{c.browser}</td>
                      <td className="px-6 py-3 text-slate-700">{c.diff_percent.toFixed(2)}%</td>
                      <td className="px-6 py-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${c.status === "passed" ? "bg-emerald-50 text-emerald-600" : c.status === "failed" ? "bg-red-50 text-red-600" : "bg-slate-100 text-slate-500"}`}>
                          {c.status}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-right">
                        {c.status === "failed" && (
                          <button onClick={() => approveVisualCheck(c.id)} className="rounded bg-amber-50 px-2.5 py-1 text-xs text-amber-700 hover:bg-amber-100">
                            Approve as baseline
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Webhooks */}
          <div className="card">
            <div className="px-6 py-4 border-b border-slate-200">
              <h3 className="font-semibold">Webhooks ({webhooks.length})</h3>
              <p className="text-sm text-slate-500 mt-0.5">Signed (HMAC-SHA256) run-completion notifications to Slack/Teams/generic receivers. URLs encrypted at rest.</p>
            </div>
            {webhooks.length === 0 ? (
              <p className="px-6 py-6 text-sm text-slate-500">No webhooks yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                    <th className="px-6 py-3">Name</th>
                    <th className="px-6 py-3">URL</th>
                    <th className="px-6 py-3">Last delivery</th>
                    <th className="px-6 py-3">Status</th>
                    <th className="px-6 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {webhooks.map((w) => (
                    <tr key={w.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-6 py-3 font-medium text-slate-800">{w.name}</td>
                      <td className="px-6 py-3 font-mono text-xs text-slate-500 max-w-64 truncate">{w.masked_url || "—"}</td>
                      <td className="px-6 py-3 text-xs text-slate-500">{w.last_delivery_at ? new Date(w.last_delivery_at).toLocaleString() : "never"}</td>
                      <td className="px-6 py-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${w.last_status === "ok" ? "bg-emerald-50 text-emerald-600" : w.last_status ? "bg-red-50 text-red-600" : "bg-slate-100 text-slate-500"}`}>
                          {w.last_status || "—"}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-right space-x-2">
                        <button onClick={() => testWebhook(w)} className="rounded bg-brand-50 px-2.5 py-1 text-xs text-brand-600 hover:bg-brand-500/30">Test</button>
                        <button onClick={() => deleteWebhook(w)} className="rounded bg-red-50 px-2.5 py-1 text-xs text-red-600 hover:bg-red-100">Delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="px-6 py-4 border-t border-slate-200 flex flex-wrap items-end gap-3">
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Name</span>
                <input value={hookForm.name} onChange={(e) => setHookForm({ ...hookForm, name: e.target.value })} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm" />
              </label>
              <label className="text-xs text-slate-500">
                <span className="block mb-1">URL</span>
                <input value={hookForm.url} onChange={(e) => setHookForm({ ...hookForm, url: e.target.value })} placeholder="https://hooks.slack.com/..." className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm w-80 font-mono" />
              </label>
              <button onClick={createWebhook} disabled={!hookForm.name.trim() || !hookForm.url.trim()} className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm font-semibold text-white">Add webhook</button>
              {recorderMsg && <span className="text-xs text-slate-500 max-w-md truncate">{recorderMsg}</span>}
            </div>
          </div>

          {/* Recorder import */}
          <div className="card p-6">
            <h3 className="font-semibold">Test recorder — import from Playwright codegen</h3>
            <p className="text-sm text-slate-500 mt-0.5">
              Record a flow with <code className="font-mono text-xs bg-slate-100 px-1 rounded">npx playwright codegen &lt;url&gt;</code>, then paste the generated actions here. They become a reviewable test case (goto / fill / click / expects).
            </p>
            <textarea
              value={recorderText}
              onChange={(e) => setRecorderText(e.target.value)}
              rows={6}
              placeholder={"await page.goto('http://demo-app:9000/login');\nawait page.getByLabel('Email').fill('user@example.com');\nawait page.getByRole('button', { name: 'Sign in' }).click();"}
              className="mt-3 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-mono focus:border-brand-500 focus:outline-none"
            />
            <div className="mt-3 flex items-center gap-3">
              <button onClick={importRecording} disabled={!recorderText.trim()} className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm font-semibold text-white">
                Import as test case
              </button>
              {recorderMsg && <span className="text-xs text-slate-500">{recorderMsg}</span>}
            </div>
          </div>
        </div>
      )}

      {/* ---------- Reports (Phase 12) ---------- */}
      {tab === "reports" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-6">
            <h3 className="font-semibold">Generate report</h3>
            <p className="text-sm text-slate-500 mt-0.5">
              CSV · Excel · PDF · HTML (with embedded evidence screenshots) · JSON — includes plan,
              executions, defects, security, performance, accessibility, and recommendations
            </p>
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Format</span>
                <select
                  value={reportReq.format}
                  onChange={(e) => setReportReq({ ...reportReq, format: e.target.value })}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                >
                  {["pdf", "html", "xlsx", "csv", "json"].map((f) => (
                    <option key={f} value={f}>{f.toUpperCase()}</option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-slate-500">
                <span className="block mb-1">Scope</span>
                <select
                  value={reportReq.runId}
                  onChange={(e) => setReportReq({ ...reportReq, runId: e.target.value })}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
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
              {reportState && <span className="text-sm text-slate-500">{reportState}</span>}
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="px-6 py-4 border-b border-slate-200">
              <h3 className="font-semibold">Reports ({reports.length})</h3>
            </div>
            {reports.length === 0 ? (
              <p className="px-6 py-8 text-sm text-slate-500">No reports generated yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
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
                    <tr key={r.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-6 py-3 text-xs text-slate-500">{new Date(r.created_at).toLocaleString()}</td>
                      <td className="px-6 py-3">
                        <span className="rounded bg-brand-50 px-1.5 py-0.5 text-xs font-mono uppercase text-brand-600">
                          {r.format}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-xs text-slate-500">
                        {r.test_run_id ? runs.find((x) => x.id === r.test_run_id)?.label ?? "run" : "whole project"}
                      </td>
                      <td className="px-6 py-3 text-xs text-slate-500">
                        {r.meta?.executions != null ? `${r.meta.executions} (${r.meta.failed ?? 0} failed)` : "—"}
                      </td>
                      <td className="px-6 py-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs font-semibold capitalize ${
                          r.status === "completed" ? "bg-emerald-50 text-emerald-600"
                          : r.status === "failed" ? "bg-red-50 text-red-600"
                          : "bg-slate-100 text-slate-500"}`}>
                          {r.status}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-right">
                        {r.status === "completed" && (
                          <button
                            onClick={() => downloadReport(r.id)}
                            className="rounded bg-slate-100 px-3 py-1.5 text-xs text-brand-600 hover:bg-slate-200"
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
