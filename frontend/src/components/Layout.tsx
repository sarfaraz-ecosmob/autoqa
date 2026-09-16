import { useCallback, useEffect, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { get, post } from "../api/client";
import { useAuth } from "../auth";

const NAV = [
  { to: "/", label: "Projects", icon: "📁", end: true },
  { to: "/about", label: "About", icon: "ℹ️" },
];

type NotifList = {
  unread: number;
  items: { id: string; title: string; body: string; link: string; read: boolean; created_at: string }[];
};

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth();
  const navigate = useNavigate();
  const [health, setHealth] = useState<string>("…");
  const [notifOpen, setNotifOpen] = useState(false);
  const [notifs, setNotifs] = useState<NotifList | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then(() => setHealth("online"))
      .catch(() => setHealth("offline"));
  }, []);

  const loadNotifs = useCallback(() => {
    if (!user) return;
    get<NotifList>("/settings/notifications?limit=8")
      .then(setNotifs)
      .catch(() => {});
  }, [user]);

  useEffect(() => {
    loadNotifs();
    const t = setInterval(loadNotifs, 30000);
    return () => clearInterval(t);
  }, [loadNotifs]);

  async function markAllRead() {
    await post("/settings/notifications/read-all").catch(() => {});
    loadNotifs();
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center text-slate-500">
        Loading…
      </div>
    );
  }

  if (!user) {
    return <>{children}</>;
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="flex min-h-screen">
        {/* Sidebar */}
        <aside className="w-60 shrink-0 border-r border-slate-200 bg-white flex flex-col">
          <div className="px-5 py-5 border-b border-slate-100">
            <Link to="/" className="text-xl font-bold tracking-tight">
              Auto<span className="text-brand-600">QA</span>
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
                      ? "bg-brand-50 text-brand-700 font-medium"
                      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                  }`
                }
              >
                <span>{item.icon}</span>
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="px-3 pb-4">
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs">
              <div className="flex items-center gap-2">
                <span
                  className={`h-2 w-2 rounded-full ${
                    health === "online" ? "bg-emerald-500" : "bg-red-500"
                  }`}
                />
                <span className="text-slate-600">API {health}</span>
              </div>
            </div>
          </div>
        </aside>

        {/* Main */}
        <div className="flex-1 flex flex-col min-w-0">
          <header className="h-14 border-b border-slate-200 bg-white/80 backdrop-blur flex items-center justify-between px-6">
            <div />
            <div className="flex items-center gap-4">
              {/* Notification bell */}
              <div className="relative">
                <button
                  onClick={() => {
                    setNotifOpen((v) => !v);
                    loadNotifs();
                  }}
                  className="relative rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                  aria-label="Notifications"
                >
                  🔔
                  {notifs && notifs.unread > 0 && (
                    <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-brand-600 px-1 text-[10px] font-semibold text-white">
                      {notifs.unread > 9 ? "9+" : notifs.unread}
                    </span>
                  )}
                </button>
                {notifOpen && (
                  <div className="absolute right-0 z-50 mt-2 w-80 rounded-xl border border-slate-200 bg-white shadow-lg">
                    <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
                      <span className="text-sm font-semibold text-slate-800">Notifications</span>
                      {notifs && notifs.items.length > 0 && (
                        <button onClick={markAllRead} className="text-xs text-brand-600 hover:text-brand-700">
                          Mark all read
                        </button>
                      )}
                    </div>
                    <div className="max-h-80 overflow-auto p-2">
                      {!notifs || notifs.items.length === 0 ? (
                        <p className="px-2 py-4 text-sm text-slate-500">Nothing new.</p>
                      ) : (
                        notifs.items.map((n) => (
                          <a
                            key={n.id}
                            href={n.link || "#"}
                            onClick={() => {
                              if (n.link) {
                                setNotifOpen(false);
                              }
                            }}
                            className={`block rounded-lg px-3 py-2 text-sm hover:bg-slate-50 ${
                              n.read ? "text-slate-600" : "bg-brand-50/60 text-slate-800"
                            }`}
                          >
                            <span className="font-medium">{n.title}</span>
                            {n.body && <span className="mt-0.5 block text-xs text-slate-500">{n.body}</span>}
                          </a>
                        ))
                      )}
                    </div>
                    <div className="border-t border-slate-100 px-4 py-2">
                      <Link to="/settings" className="text-xs text-brand-600 hover:text-brand-700">
                        Notification settings →
                      </Link>
                    </div>
                  </div>
                )}
              </div>
              <span className="text-sm text-slate-500">{user.email}</span>
              <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-700 capitalize">
                {user.role}
              </span>
              <Link to="/settings" className="text-sm text-slate-500 hover:text-slate-800">
                Settings
              </Link>
              <button
                onClick={() => {
                  logout();
                  navigate("/login");
                }}
                className="text-sm text-slate-500 hover:text-slate-800"
              >
                Sign out
              </button>
            </div>
          </header>

          <main className="flex-1 overflow-auto bg-gradient-to-b from-brand-50/50 via-slate-50 to-slate-50">
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
