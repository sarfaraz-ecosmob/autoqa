import { afterEach, describe, expect, it, vi } from "vitest";
import { clearTokens, get, setTokens, ApiError } from "./client";

afterEach(() => {
  clearTokens();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("api client", () => {
  it("sends GET and parses JSON", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const data = await get<{ ok: boolean }>("/health");
    expect(data.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith("/api/health", expect.any(Object));
  });

  it("attaches bearer token when present", async () => {
    setTokens("tok-123", "ref-456");
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await get("/me");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer tok-123");
  });

  it("throws ApiError with server detail on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Project not found" }), { status: 404 }),
      ),
    );
    try {
      await get("/projects/xyz");
      expect.fail("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(404);
      expect((err as ApiError).message).toContain("Project not found");
    }
  });

  it("clears tokens and redirects on 401", async () => {
    setTokens("expired", "expired");
    const hrefSpy = vi.fn();
    Object.defineProperty(window, "location", {
      value: { ...window.location, get href() { return ""; }, set href(v: string) { hrefSpy(v); } },
      configurable: true,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 401 })),
    );
    await expect(get("/me")).rejects.toThrow("Session expired");
    expect(localStorage.getItem("autoqa_token")).toBeNull();
  });
});
