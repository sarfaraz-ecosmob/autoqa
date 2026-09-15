import { describe, expect, it, vi } from "vitest";
import { getJson } from "./client";

describe("api client", () => {
  it("prefixes /api and parses JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const data = await getJson<{ ok: boolean }>("/health");
    expect(data.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith("/api/health");
  });

  it("throws on non-2xx", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("nope", { status: 500 })),
    );
    await expect(getJson("/health")).rejects.toThrow("500");
  });
});
