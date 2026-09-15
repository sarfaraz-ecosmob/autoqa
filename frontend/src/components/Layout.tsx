import { useEffect, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

const NAV = [
  { to: "/", label: "Projects", icon: "📁", end: true },
  { to: "/about", label: "About", icon: "ℹ️" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth();
  const navigate = useNavigate();
  const [health, setHealth] = useState<string>("…");

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then(() => setHealth("online"))
      .catch(() => setHealth("offline"));
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center text-slate-400">
        Loading…
      </div>
    );
  }

  if (!user) {
    return <>{children}</>;
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="flex min-h-screen">
        {/* Sidebar */}
        <aside className="w-60 shrink-0 border-r border-slate-800 bg-slate-900 flex flex-col">
          <div className="px-5 py-5 border-b border-slate-800">
            <Link to="/" className="text-xl font-bold">
              Auto<span className="text-brand-500">QA</span>
            </Link>
            <p className="text-[11px] text-slate-500 mt-0.5">Autonomous QA Platform</p>
          </div>

          <nav className="flex-1 px-3 py-4 space-y-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }: { isActive: boolean }) =>
                  `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                    isActive
                      ? "bg-brand-600/20 text-brand-400 font-medium"
                      : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                  }`
                }
              >
                <span>{item.icon}</span>
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="px-3 pb-4">
            <div className="rounded-lg bg-slate-800/60 px-3 py-2.5 text-xs">
              <div className="flex items-center gap-2">
                <span
                  className={`h-2 w-2 rounded-full ${
                    health === "online" ? "bg-emerald-500" : "bg-red-500"
                  }`}
                />
                <span className="text-slate-400">API {health}</span>
              </div>
            </div>
          </div>
        </aside>

        {/* Main */}
        <div className="flex-1 flex flex-col min-w-0">
          <header className="h-14 border-b border-slate-800 bg-slate-900/60 backdrop-blur flex items-center justify-between px-6">
            <div />
            <div className="flex items-center gap-4">
              <span className="text-sm text-slate-400">{user.email}</span>
              <span className="rounded-full bg-slate-800 px-2.5 py-0.5 text-xs text-slate-300 capitalize">
                {user.role}
              </span>
              <button
                onClick={() => {
                  logout();
                  navigate("/login");
                }}
                className="text-sm text-slate-400 hover:text-slate-200"
              >
                Sign out
              </button>
            </div>
          </header>

          <main className="flex-1 overflow-auto">{children}</main>
        </div>
      </div>
    </div>
  );
}
