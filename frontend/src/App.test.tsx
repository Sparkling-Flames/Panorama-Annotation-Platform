import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

function jsonResponse(body: object): Response {
  return { json: async () => body, ok: true, status: 200 } as Response;
}

describe("App", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
    sessionStorage.clear();
    vi.restoreAllMocks();
    document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
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
    expect(
      screen.getByRole("heading", { name: "本地预测导入 / Local prediction import" }),
    ).toBeInTheDocument();
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

  it("shows a retryable session error instead of pretending a network failure is anonymous", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(jsonResponse({ authenticated: false }));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取会话");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("lets an anonymous user log in without storing credentials", async () => {
    document.cookie = "csrftoken=test-csrf; path=/";
    const testPassword = ["temporary", "password"].join("-");
    let sessionReads = 0;
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
      async (input) => {
        const url = String(input);
        if (url === "/api/auth/session") {
          sessionReads += 1;
          return jsonResponse(
            sessionReads === 1
              ? { authenticated: false }
              : { authenticated: true, must_change_password: true, role: "worker" },
          );
        }
        if (url === "/api/auth/login") {
          return jsonResponse({ must_change_password: true, workspace_access: false });
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.change(await screen.findByLabelText("用户名"), { target: { value: "worker-01" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: testPassword } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("heading", { name: "首次修改密码" })).toBeInTheDocument();
    const loginCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/auth/login");
    expect(JSON.parse(String(loginCall?.[1]?.body))).toEqual({
      password: testPassword,
      username: "worker-01",
    });
    expect(localStorage).toHaveLength(0);
    expect(sessionStorage).toHaveLength(0);
  });

  it("lets a worker replace the temporary password through the real API", async () => {
    document.cookie = "csrftoken=test-csrf; path=/";
    const pendingSession = new Promise<Response>(() => undefined);
    let sessionReads = 0;
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
      async (input) => {
        const url = String(input);
        if (url === "/api/auth/session") {
          sessionReads += 1;
          return sessionReads === 1
            ? jsonResponse({ authenticated: true, must_change_password: true, role: "worker" })
            : pendingSession;
        }
        if (url === "/api/auth/change-password") {
          return { ok: true, status: 204 } as Response;
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.change(await screen.findByLabelText("当前密码"), {
      target: { value: "temporary-password" },
    });
    fireEvent.change(screen.getByLabelText("新密码"), {
      target: { value: "A-valid-new-password-2026" },
    });
    fireEvent.change(screen.getByLabelText("确认新密码"), {
      target: { value: "A-valid-new-password-2026" },
    });
    fireEvent.click(screen.getByRole("button", { name: "修改密码" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url) === "/api/auth/change-password"),
      ).toBe(true),
    );
    const changeCall = fetchMock.mock.calls.find(
      ([url]) => String(url) === "/api/auth/change-password",
    );
    expect(JSON.parse(String(changeCall?.[1]?.body))).toEqual({
      current_password: "temporary-password",
      new_password: "A-valid-new-password-2026",
    });
    expect(await screen.findByText("正在读取会话…")).toBeInTheDocument();
  });
});
