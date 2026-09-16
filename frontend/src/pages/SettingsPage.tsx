import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, post, put, ApiError } from "../api/client";
import { useAuth } from "../auth";

const CARD = "rounded-xl border border-slate-200 bg-white p-5 shadow-sm";

// ---------------------------------------------------------------- account

function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (next !== confirm) {
      setMsg({ ok: false, text: "New passwords do not match" });
      return;
    }
    setBusy(true);
    try {
      await post("/settings/account/password", { current_password: current, new_password: next });
      setMsg({ ok: true, text: "Password updated" });
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Failed to update password" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={CARD}>
      <h2 className="text-base font-semibold text-slate-900">Account</h2>
      <p className="mt-0.5 text-sm text-slate-500">Change your password. You stay signed in.</p>
      <form onSubmit={submit} className="mt-4 space-y-3 max-w-md">
        <input
          type="password"
          placeholder="Current password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
        />
        <input
          type="password"
          placeholder="New password (min 8 chars)"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          minLength={8}
          required
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
        />
        <input
          type="password"
          placeholder="Confirm new password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          minLength={8}
          required
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
        />
        {msg && (
          <p className={`text-sm ${msg.ok ? "text-emerald-600" : "text-red-600"}`}>{msg.text}</p>
        )}
        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {busy ? "Saving…" : "Update password"}
        </button>
      </form>
    </div>
  );
}

// ------------------------------------------------------------- ai provider

type AISettings = {
  provider: string;
  model: string;
  base_url: string;
  has_key: boolean;
  masked_key: string | null;
  source: string;
  detected_free_model?: string | null;
};

const PROVIDERS = [
  { value: "openrouter", label: "OpenRouter (auto-picks best free model)" },
  { value: "openai", label: "OpenAI / OpenAI-compatible (vLLM, Ollama)" },
  { value: "none", label: "Disabled (heuristic fallbacks)" },
];

function AISettingsCard() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [cfg, setCfg] = useState<AISettings | null>(null);
  const [provider, setProvider] = useState("openrouter");
  const [model, setModel] = useState("auto");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get<AISettings>("/settings/ai")
      .then((s) => {
        setCfg(s);
        setProvider(s.provider === "none" && s.has_key ? "openrouter" : s.provider);
        setModel(s.model || "auto");
        setBaseUrl(s.base_url || "");
      })
      .catch(() => setMsg({ ok: false, text: "Could not load AI settings" }));
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    setBusy(true);
    try {
      const body: Record<string, string> = { provider, model, base_url: baseUrl };
      if (apiKey.trim()) body.api_key = apiKey.trim();
      await put("/settings/ai", body);
      setApiKey("");
      const fresh = await get<AISettings>("/settings/ai");
      setCfg(fresh);
      setMsg({ ok: true, text: "AI settings saved" });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Failed to save" });
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    setMsg(null);
    setBusy(true);
    try {
      const res = await post<{ ok: boolean; model?: string; error?: string }>("/settings/ai/test");
      setMsg(
        res.ok
          ? { ok: true, text: `Connected — model: ${res.model ?? "?"}` }
          : { ok: false, text: res.error ?? "Connection failed" }
      );
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Test failed" });
    } finally {
      setBusy(false);
    }
  }

  if (!isAdmin) {
    return (
      <div className={CARD}>
        <h2 className="text-base font-semibold text-slate-900">AI Assistant</h2>
        <p className="mt-1 text-sm text-slate-500">
          {cfg?.has_key
            ? `Provider configured (${cfg.provider}, key from ${cfg.source}). Ask an admin to change it.`
            : "No AI provider configured — the assistant uses deterministic heuristics. An admin can add an API key here."}
        </p>
      </div>
    );
  }

  return (
    <div className={CARD}>
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-900">AI Assistant</h2>
          <p className="mt-0.5 text-sm text-slate-500">
            Key is encrypted at rest and never displayed again — only a masked preview.
          </p>
        </div>
        {cfg && (
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
              cfg.has_key ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"
            }`}
          >
            {cfg.has_key ? `Key set (${cfg.source})` : "No key"}
          </span>
        )}
      </div>

      {cfg?.masked_key && (
        <p className="mt-2 text-xs text-slate-500">Stored key: {cfg.masked_key}</p>
      )}
      {cfg?.detected_free_model && (
        <p className="mt-1 text-xs text-slate-500">
          Auto-selected free model: <span className="font-medium text-slate-700">{cfg.detected_free_model}</span>
        </p>
      )}

      <form onSubmit={save} className="mt-4 grid gap-3 max-w-xl">
        <label className="text-sm">
          <span className="text-slate-600">Provider</span>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
          >
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="text-slate-600">Model</span>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="auto = detect best free model"
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
          />
        </label>
        {provider === "openai" && (
          <label className="text-sm">
            <span className="text-slate-600">Base URL (optional for self-hosted)</span>
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          </label>
        )}
        <label className="text-sm">
          <span className="text-slate-600">
            API key {cfg?.has_key ? "(leave blank to keep current)" : ""}
          </span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={cfg?.has_key ? "sk-… (kept)" : "sk-or-v1-…"}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
          />
        </label>
        {msg && (
          <p className={`text-sm ${msg.ok ? "text-emerald-600" : "text-red-600"}`}>{msg.text}</p>
        )}
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={busy}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {busy ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={testConnection}
            disabled={busy}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Test connection
          </button>
        </div>
      </form>
    </div>
  );
}

// ------------------------------------------------------------ notifications

const EVENT_LABELS: Record<string, string> = {
  run_completed: "Run completed",
  run_failed_tests: "Run finished with failed tests",
  security_scan_completed: "Security scan completed",
  report_ready: "Report ready",
  a11y_critical: "Critical accessibility violations",
  perf_breach: "Performance threshold breach",
};

type NotifSettings = { events: Record<string, boolean>; email_enabled: boolean; email_address: string };
type NotifList = {
  unread: number;
  items: {
    id: string;
    kind: string;
    title: string;
    body: string;
    link: string;
    read: boolean;
    created_at: string;
  }[];
};

function NotificationsCard() {
  const [settings, setSettings] = useState<NotifSettings | null>(null);
  const [list, setList] = useState<NotifList | null>(null);

  useEffect(() => {
    get<NotifSettings>("/settings/notifications/settings").then(setSettings).catch(() => {});
    load();
  }, []);

  function load() {
    get<NotifList>("/settings/notifications?limit=20").then(setList).catch(() => {});
  }

  async function toggleEvent(key: string, value: boolean) {
    if (!settings) return;
    const events = { ...settings.events, [key]: value };
    setSettings({ ...settings, events });
    await put("/settings/notifications/settings", { events }).catch(() => {});
  }

  async function markRead(id: string) {
    await post(`/settings/notifications/${id}/read`).catch(() => {});
    load();
  }

  async function markAll() {
    await post("/settings/notifications/read-all").catch(() => {});
    load();
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className={CARD}>
        <h2 className="text-base font-semibold text-slate-900">Notification settings</h2>
        <p className="mt-0.5 text-sm text-slate-500">Choose which events appear in your inbox.</p>
        <div className="mt-4 space-y-2">
          {settings &&
            Object.entries(settings.events).map(([key, on]) => (
              <label key={key} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-sm">
                <span className="text-slate-700">{EVENT_LABELS[key] ?? key}</span>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={(e) => toggleEvent(key, e.target.checked)}
                  className="h-4 w-4 accent-brand-600"
                />
              </label>
            ))}
        </div>
      </div>

      <div className={CARD}>
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900">
            Inbox{" "}
            {list && list.unread > 0 && (
              <span className="ml-1 rounded-full bg-brand-600 px-2 py-0.5 text-xs text-white">{list.unread}</span>
            )}
          </h2>
          {list && list.items.length > 0 && (
            <button onClick={markAll} className="text-xs text-brand-600 hover:text-brand-700">
              Mark all read
            </button>
          )}
        </div>
        <div className="mt-3 space-y-2">
          {!list || list.items.length === 0 ? (
            <p className="text-sm text-slate-500">No notifications yet.</p>
          ) : (
            list.items.map((n) => (
              <div
                key={n.id}
                className={`rounded-lg border px-3 py-2 text-sm ${
                  n.read ? "border-slate-200 bg-white" : "border-brand-200 bg-brand-50/60"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="font-medium text-slate-800">{n.title}</p>
                    {n.body && <p className="mt-0.5 text-xs text-slate-500">{n.body}</p>}
                  </div>
                  {!n.read && (
                    <button onClick={() => markRead(n.id)} className="text-xs text-brand-600 hover:text-brand-700">
                      Mark read
                    </button>
                  )}
                </div>
                {n.link && (
                  <a href={n.link} className="mt-1 inline-block text-xs text-brand-600 hover:underline">
                    Open →
                  </a>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- page

export default function SettingsPage() {
  const navigate = useNavigate();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (!loading && !user) navigate("/login");
  }, [loading, user, navigate]);

  if (loading || !user) return null;

  return (
    <div className="mx-auto max-w-5xl px-6 py-8 space-y-4">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-slate-900">Settings</h1>
        <p className="text-sm text-slate-500">
          Signed in as {user.email} · {user.role}
        </p>
      </div>
      <PasswordCard />
      <AISettingsCard />
      <NotificationsCard />
    </div>
  );
}
