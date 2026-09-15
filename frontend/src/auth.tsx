import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import * as api from "./api/client";

interface User {
  id: string;
  email: string;
  role: string;
  name: string;
}

interface AuthCtx {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthCtx>({
  user: null,
  loading: true,
  login: async () => {},
  register: async () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!api.getToken()) {
      setLoading(false);
      return;
    }
    api
      .get<User>("/auth/me")
      .then(setUser)
      .catch(() => api.clearTokens())
      .finally(() => setLoading(false));
  }, []);

  async function login(email: string, password: string) {
    const tokens = await api.post<{ access_token: string; refresh_token: string }>("/auth/login", {
      email,
      password,
    });
    api.setTokens(tokens.access_token, tokens.refresh_token);
    setUser(await api.get<User>("/auth/me"));
  }

  async function register(email: string, password: string, name: string) {
    await api.post("/auth/register", { email, password, name });
    await login(email, password);
  }

  function logout() {
    api.clearTokens();
    setUser(null);
  }

  return <Ctx.Provider value={{ user, loading, login, register, logout }}>{children}</Ctx.Provider>;
}

export function useAuth() {
  return useContext(Ctx);
}
