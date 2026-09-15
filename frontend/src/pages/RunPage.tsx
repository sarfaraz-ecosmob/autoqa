import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import * as api from "../api/client";

interface Live {
  status: string;
  counters: Record<string, number>;
  recent: {
    ref: string;
    status: string;
    attempt: number;
    browser: string;
    duration_ms: number | null;
    actual_result: string;
    log: { action: string; result?: string }[];
    updated_at: string;
  }[];
}

interface RunRow {
  id: string;
  label: string;
  status: string;
  browsers: string[];
}

const STATUS_STYLES: Record<string, string> = {
  passed: "text-emerald-400",
  failed: "text-red-400",
  running: "text-brand-400",
  queued: "text-slate-500",
  skipped: "text-slate-500",
  blocked: "text-amber-400",
};

interface LogLine {
  ts: string;
  text: string;
}

export default function RunPage() {
  const { id = "", runId = "" } = useParams();
  const [run, setRun] = useState<RunRow | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [logLines, setLogLines] = useState<LogLine[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const loadLive = useCallback(async () => {
    try {
      const data = await api.get<Live>(`/projects/${id}/test-runs/${runId}/live`);
      setLive(data);
    } catch {
      /* run may not exist yet */
    }
  }, [id, runId]);

  useEffect(() => {
    api.get<RunRow[]>(`/projects/${id}/test-runs`).then((rows) => {
      setRun(rows.find((r) => r.id === runId) ?? null);
    });
    loadLive();
  }, [id, runId, loadLive]);

  // SSE stream with polling fallback
  useEffect(() => {
    const es = new EventSource(`/api/projects/${id}/test-runs/${runId}/stream`);
    esRef.current = es;
    es.onopen = () => setConnected(true);
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        const time = new Date(ev.ts ?? Date.now()).toLocaleTimeString();
        if (ev.event === "test_started") {
          setLogLines((prev) => [...prev.slice(-200), { ts: time, text: `▶ ${ev.ref} started (attempt ${ev.attempt}, ${ev.browser})` }]);
        } else if (ev.event === "test_finished") {
          setLogLines((prev) => [...prev.slice(-200), { ts: time, text: `${ev.status === "passed" ? "✔" : ev.status === "failed" ? "✖" : "•"} ${ev.ref} ${ev.status.toUpperCase()} in ${ev.duration_ms}ms` }]);
        } else if (ev.event === "run_completed") {
          setLogLines((prev) => [...prev.slice(-200), { ts: time, text: "■ run completed" }]);
        }
        loadLive(); // refresh counters on every event
      } catch {
        /* ignore malformed events */
      }
    };
    es.onerror = () => setConnected(false);

    const poll = setInterval(loadLive, 5000); // fallback refresh
    return () => {
      es.close();
      clearInterval(poll);
    };
  }, [id, runId, loadLive]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [logLines]);

  async function control(action: string) {
    await api.post(`/projects/${id}/test-runs/${runId}/${action}`);
    await loadLive();
  }

  const c = live?.counters ?? {};
  const pct = c.percent ?? 0;

  return (
    <div className="p-8 max-w-6xl">
      <div className="flex items-start justify-between mb-6">
        <div>
          <Link to={`/projects/${id}`} className="text-slate-500 hover:text-slate-300 text-sm">
            ← {run?.label ?? "Project"}
          </Link>
          <h1 className="mt-2 text-2xl font-bold flex items-center gap-3">
            Execution
            <span className={`rounded-full px-2.5 py-0.5 text-xs capitalize ${
              live?.status === "completed" ? "bg-emerald-500/10 text-emerald-400"
              : live?.status === "running" ? "bg-brand-500/10 text-brand-400"
              : "bg-slate-800 text-slate-400"}`}>
              {live?.status ?? "…"}
            </span>
          </h1>
        </div>
        <div className="flex gap-2">
          {live?.status === "running" && (
            <button onClick={() => control("pause")} className="rounded-lg bg-slate-800 hover:bg-slate-700 px-4 py-2 text-sm">Pause</button>
          )}
          {live?.status === "paused" && (
            <button onClick={() => control("resume")} className="rounded-lg bg-emerald-600 hover:bg-emerald-500 px-4 py-2 text-sm">Resume</button>
          )}
          {(live?.status === "running" || live?.status === "paused") && (
            <button onClick={() => control("stop")} className="rounded-lg bg-red-600/20 text-red-400 hover:bg-red-600/30 px-4 py-2 text-sm">Stop</button>
          )}
        </div>
      </div>

      {/* Counters */}
      <div className="grid grid-cols-3 md:grid-cols-7 gap-3 mb-4">
        {[
          ["Total", c.total ?? 0, "text-slate-200"],
          ["Queued", c.queued ?? 0, "text-slate-400"],
          ["Running", c.running ?? 0, "text-brand-400"],
          ["Passed", c.passed ?? 0, "text-emerald-400"],
          ["Failed", c.failed ?? 0, "text-red-400"],
          ["Skipped", c.skipped ?? 0, "text-slate-400"],
          ["Blocked", c.blocked ?? 0, "text-amber-400"],
        ].map(([label, value, cls]) => (
          <div key={label as string} className="rounded-xl border border-slate-800 bg-slate-900 px-4 py-3">
            <p className="text-xs text-slate-500">{label}</p>
            <p className={`text-2xl font-bold ${cls}`}>{value}</p>
          </div>
        ))}
      </div>

      {/* Progress */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-5 mb-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-slate-400">Progress</span>
          <span className="text-sm font-semibold">{pct}%</span>
        </div>
        <div className="h-2.5 rounded-full bg-slate-800 overflow-hidden">
          <div className="h-full bg-gradient-to-r from-brand-600 to-brand-400 transition-all duration-500" style={{ width: `${pct}%` }} />
        </div>
        <p className="mt-2 text-xs text-slate-500">
          {connected ? "● live stream connected" : "○ polling every 5s"}
        </p>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {/* Live log */}
        <div className="rounded-xl border border-slate-800 bg-slate-900">
          <div className="px-5 py-3 border-b border-slate-800 text-sm font-semibold">Live log</div>
          <div ref={logRef} className="h-80 overflow-auto p-4 font-mono text-xs space-y-1">
            {logLines.length === 0 && <p className="text-slate-600">Waiting for events…</p>}
            {logLines.map((l, i) => (
              <p key={i} className="text-slate-300">
                <span className="text-slate-600">{l.ts}</span> {l.text}
              </p>
            ))}
          </div>
          <div className="px-5 py-2 border-t border-slate-800 text-[11px] text-slate-600">
            Full step logs appear in each execution's evidence after completion
          </div>
        </div>

        {/* Recent executions */}
        <div className="rounded-xl border border-slate-800 bg-slate-900">
          <div className="px-5 py-3 border-b border-slate-800 text-sm font-semibold">Executions</div>
          <div className="h-80 overflow-auto">
            <table className="w-full text-sm">
              <tbody>
                {(live?.recent ?? []).map((r) => (
                  <tr key={r.ref + r.attempt} className="border-b border-slate-800/50 last:border-0">
                    <td className="px-4 py-2 font-mono text-xs text-brand-400">{r.ref}</td>
                    <td className={`px-2 py-2 text-xs font-semibold uppercase ${STATUS_STYLES[r.status] ?? ""}`}>
                      {r.status}
                    </td>
                    <td className="px-2 py-2 text-xs text-slate-500">{r.browser}</td>
                    <td className="px-2 py-2 text-xs text-slate-500">{r.duration_ms ?? "—"}ms</td>
                    <td className="px-4 py-2 text-xs text-slate-400 truncate max-w-40">{r.actual_result || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
