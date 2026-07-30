import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

function jsonResponse(body: object): Response {
  return { json: async () => body, ok: true, status: 200 } as Response;
}

describe("App", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the platform shell without exposing admin controls to an anonymous user", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ authenticated: false })));

    render(<App />);

    expect(
      screen.getByRole("heading", { name: "Panorama Annotation Platform" }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(
        screen.queryByRole("heading", { name: "管理员 COS 媒体导入" }),
      ).not.toBeInTheDocument();
    });
  });

  it("shows the media import route only to an administrator", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ authenticated: true, must_change_password: false, role: "admin" }),
        ),
    );

    render(<App />);

    expect(await screen.findByRole("heading", { name: "管理员 COS 媒体导入" })).toBeInTheDocument();
  });

  it("does not show media import controls to a worker", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ authenticated: true, must_change_password: true, role: "worker" }),
        ),
    );

    render(<App />);

    await waitFor(() => {
      expect(
        screen.queryByRole("heading", { name: "管理员 COS 媒体导入" }),
      ).not.toBeInTheDocument();
    });
  });
});
