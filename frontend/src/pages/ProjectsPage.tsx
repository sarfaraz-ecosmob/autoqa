import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import * as api from "../api/client";

interface Project {
  id: string;
  name: string;
  description: string;
  base_url: string;
  authorization_confirmed: boolean;
  created_at: string;
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [description, setDescription] = useState("");
  const [authConfirmed, setAuthConfirmed] = useState(false);

  const load = useCallback(async () => {
    try {
      setProjects(await api.get<Project[]>("/projects"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load projects");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/projects", {
        name,
        base_url: baseUrl,
        description,
      });
      setName("");
      setBaseUrl("");
      setDescription("");
      setAuthConfirmed(false);
      setShowForm(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(id: string, projectName: string) {
    if (!window.confirm(`Delete project "${projectName}"? This cannot be undone.`)) return;
    try {
      await api.del(`/projects/${id}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  return (
    <div className="p-8 max-w-6xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Projects</h1>
          <p className="text-sm text-slate-400 mt-1">
            QA projects targeting a website under test
          </p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded-lg bg-brand-600 hover:bg-brand-500 px-4 py-2 text-sm font-semibold"
        >
          {showForm ? "Cancel" : "+ New project"}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {showForm && (
        <form
          onSubmit={onCreate}
          className="mb-6 rounded-xl border border-slate-800 bg-slate-900 p-6 space-y-4"
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Name</label>
              <input
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
                placeholder="Demo Shop QA"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Target URL
              </label>
              <input
                required
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
                placeholder="https://staging.example.com"
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">
              Description <span className="text-slate-500">(optional)</span>
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
              placeholder="What are you testing?"
            />
          </div>
          <label className="flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 cursor-pointer">
            <input
              type="checkbox"
              required
              checked={authConfirmed}
              onChange={(e) => setAuthConfirmed(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-amber-500"
            />
            <span className="text-sm text-amber-200">
              I confirm I have <strong>authorization to test</strong> this application (required
              before scanning — spec §2 step 4).
            </span>
          </label>
          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="rounded-lg px-4 py-2 text-sm text-slate-400 hover:text-slate-200"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-5 py-2 text-sm font-semibold"
            >
              {busy ? "Creating…" : "Create project"}
            </button>
          </div>
        </form>
      )}

      {projects.length === 0 && !showForm ? (
        <div className="rounded-xl border border-dashed border-slate-800 p-12 text-center text-slate-500">
          No projects yet. Create one to start testing.
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {projects.map((p) => (
            <Link
              key={p.id}
              to={`/projects/${p.id}`}
              className="group rounded-xl border border-slate-800 bg-slate-900 p-5 hover:border-brand-500/50 transition-colors"
            >
              <div className="flex items-start justify-between">
                <div className="min-w-0">
                  <h3 className="font-semibold text-slate-100 group-hover:text-brand-400">
                    {p.name}
                  </h3>
                  <p className="mt-0.5 truncate text-sm text-slate-500">{p.base_url}</p>
                </div>
                <span
                  className={`ml-3 shrink-0 rounded-full px-2.5 py-0.5 text-xs ${
                    p.authorization_confirmed
                      ? "bg-emerald-500/10 text-emerald-400"
                      : "bg-amber-500/10 text-amber-400"
                  }`}
                >
                  {p.authorization_confirmed ? "authorized" : "unconfirmed"}
                </span>
              </div>
              {p.description && (
                <p className="mt-2 line-clamp-2 text-sm text-slate-400">{p.description}</p>
              )}
              <div className="mt-4 flex items-center justify-between">
                <span className="text-xs text-slate-600">
                  {new Date(p.created_at).toLocaleDateString()}
                </span>
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    onDelete(p.id, p.name);
                  }}
                  className="text-xs text-slate-600 hover:text-red-400"
                >
                  Delete
                </button>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
