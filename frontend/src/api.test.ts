import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "./api";

describe("apiFetch", () => {
  afterEach(() => {
    document.cookie = "csrftoken=; Max-Age=0; Path=/";
    vi.restoreAllMocks();
  });

  it("bootstraps CSRF before the first unsafe request", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      if (input === "/api/auth/csrf") {
        document.cookie = "csrftoken=browser-token; Path=/";
      }
      return { ok: true } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/admin/media/imports/preview", {
      body: "{}",
      method: "POST",
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/auth/csrf", {
      credentials: "same-origin",
    });
    const request = fetchMock.mock.calls[1];
    expect(request[0]).toBe("/api/admin/media/imports/preview");
    expect(request[1]?.credentials).toBe("same-origin");
    expect(new Headers(request[1]?.headers).get("X-CSRFToken")).toBe("browser-token");
  });
});
