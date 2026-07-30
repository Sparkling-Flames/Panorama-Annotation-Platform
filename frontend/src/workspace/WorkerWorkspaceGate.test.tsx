import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkerWorkspaceGate } from "./WorkerWorkspaceGate";
import { WorkspaceTabCoordinator, type WorkspaceLockManager } from "./workspaceTabCoordinator";

class FakeWorkspaceLockManager implements WorkspaceLockManager {
  private locked = false;

  async request<T>(
    _name: string,
    _options: { ifAvailable: true; mode: "exclusive" },
    callback: (lock: Lock | null) => Promise<T> | T,
  ): Promise<T> {
    if (this.locked) {
      return callback(null);
    }
    this.locked = true;
    try {
      return await callback({ name: "workspace", mode: "exclusive" } as Lock);
    } finally {
      this.locked = false;
    }
  }

  isLocked(): boolean {
    return this.locked;
  }
}

function jsonResponse(body: object, status = 200): Response {
  return { json: async () => body, ok: status >= 200 && status < 300, status } as Response;
}

describe("WorkerWorkspaceGate", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
  });

  afterEach(() => {
    cleanup();
    document.cookie = "csrftoken=; Max-Age=0; Path=/";
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("acquires the browser lock before the server lease and releases it on unmount", async () => {
    const lockManager = new FakeWorkspaceLockManager();
    vi.stubGlobal("navigator", { locks: lockManager });
    const fetchMock = vi.fn(async () => {
      expect(lockManager.isLocked()).toBe(true);
      return jsonResponse(
        { lease_expires_at: "2026-07-30T12:00:00Z", workspace_state: "editable" },
        201,
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const view = render(<WorkerWorkspaceGate />);

    expect(await screen.findByText("工作区可编辑。")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/workspace/acquire",
      expect.objectContaining({
        body: expect.stringContaining('"takeover":false'),
        method: "POST",
      }),
    );
    view.unmount();
    await waitFor(() => expect(lockManager.isLocked()).toBe(false));
  });

  it("waits locally, then retries after the first browser tab releases its lock", async () => {
    const lockManager = new FakeWorkspaceLockManager();
    const firstTab = new WorkspaceTabCoordinator(lockManager);
    await firstTab.acquire();
    vi.stubGlobal("navigator", { locks: lockManager });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkerWorkspaceGate />);

    expect(await screen.findByText("此浏览器已有另一个可编辑标签页。")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    firstTab.release();
    await waitFor(() => expect(lockManager.isLocked()).toBe(false));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ lease_expires_at: "2026-07-30T12:00:00Z", workspace_state: "editable" }, 200),
    );
    fireEvent.click(screen.getByRole("button", { name: "重试进入工作区" }));

    expect(await screen.findByText("工作区可编辑。")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("requires explicit confirmation before taking over a server lease", async () => {
    const lockManager = new FakeWorkspaceLockManager();
    vi.stubGlobal("navigator", { locks: lockManager });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ error: { code: "workspace_takeover_required" } }, 409))
      .mockResolvedValueOnce(
        jsonResponse(
          { lease_expires_at: "2026-07-30T12:00:00Z", workspace_state: "editable" },
          201,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkerWorkspaceGate />);

    const takeoverButton = await screen.findByRole("button", { name: "接管工作区" });
    expect(screen.queryByText("工作区可编辑。")).not.toBeInTheDocument();
    fireEvent.click(takeoverButton);

    expect(await screen.findByText("工作区可编辑。")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/workspace/acquire",
      expect.objectContaining({
        body: expect.stringContaining('"takeover":true'),
        method: "POST",
      }),
    );
  });

  it("leaves editable state when a lease renewal loses the network", async () => {
    vi.useFakeTimers();
    const lockManager = new FakeWorkspaceLockManager();
    vi.stubGlobal("navigator", { locks: lockManager });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(
          { lease_expires_at: "2026-07-30T12:00:00Z", workspace_state: "editable" },
          201,
        ),
      )
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkerWorkspaceGate />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("工作区可编辑。")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000);
    });

    expect(screen.queryByText("工作区可编辑。")).not.toBeInTheDocument();
    expect(screen.getByText("网络中断，工作区不可编辑。")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
