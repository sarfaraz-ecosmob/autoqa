import { useEffect, useState } from "react";

interface Health {
  status: string;
  environment: string;
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then(setHealth)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-4xl px-6 py-16">
        <h1 className="text-4xl font-bold tracking-tight">
          AutoQA <span className="text-brand-500">Platform</span>
        </h1>
        <p className="mt-2 text-slate-400">
          Autonomous end-to-end QA automation — Phase 0 skeleton.
        </p>

        <div className="mt-10 rounded-xl border border-slate-800 bg-slate-900 p-6">
          <h2 className="text-lg font-semibold">Backend status</h2>
          {error && (
            <p className="mt-2 text-sm text-red-400">Error: {error}</p>
          )}
          {health ? (
            <p className="mt-2 text-sm text-emerald-400">
              API healthy ({health.environment}) — nginx → FastAPI → Postgres
              stack is up.
            </p>
          ) : (
            !error && <p className="mt-2 text-sm text-slate-500">Checking…</p>
          )}
        </div>
      </div>
    </div>
  );
}
