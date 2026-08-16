import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
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

class StrictModeWorkspaceLockManager implements WorkspaceLockManager {
  async request<T>(
    _name: string,
    _options: { ifAvailable: true; mode: "exclusive" },
    callback: (lock: Lock | null) => Promise<T> | T,
  ): Promise<T> {
    return callback({ name: "workspace", mode: "exclusive" } as Lock);
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
    sessionStorage.clear();
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

    const view = render(
      <WorkerWorkspaceGate>
        <p>编辑器已挂载</p>
      </WorkerWorkspaceGate>,
    );

    expect(await screen.findByText("工作区可编辑。")).toBeInTheDocument();
    expect(screen.getByText("编辑器已挂载")).toBeInTheDocument();
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

  it("PAP-IAM-SC-012 reuses its non-secret workspace identity when the same tab reloads", async () => {
    const lockManager = new FakeWorkspaceLockManager();
    vi.stubGlobal("navigator", { locks: lockManager });
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(
          { lease_expires_at: "2026-07-30T12:00:00Z", workspace_state: "editable" },
          201,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const firstView = render(<WorkerWorkspaceGate />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const firstIdentity = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body)) as {
      client_instance_id: string;
      tab_id: string;
    };
    firstView.unmount();
    await waitFor(() => expect(lockManager.isLocked()).toBe(false));

    render(<WorkerWorkspaceGate />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const secondIdentity = JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body)) as {
      client_instance_id: string;
      tab_id: string;
    };

    expect(secondIdentity).toEqual(firstIdentity);
    expect(Object.keys(sessionStorage).sort()).toEqual([
      "panorama.workspace.client-instance-id",
      "panorama.workspace.tab-id",
    ]);
    expect(localStorage).toHaveLength(0);
  });

  it("waits locally, then retries after the first browser tab releases its lock", async () => {
    const lockManager = new FakeWorkspaceLockManager();
    const firstTab = new WorkspaceTabCoordinator(lockManager);
    await firstTab.acquire();
    vi.stubGlobal("navigator", { locks: lockManager });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WorkerWorkspaceGate>
        <p>编辑器已挂载</p>
      </WorkerWorkspaceGate>,
    );

    expect(await screen.findByText("此浏览器已有另一个可编辑标签页。")).toBeInTheDocument();
    expect(screen.queryByText("编辑器已挂载")).not.toBeInTheDocument();
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

    render(
      <WorkerWorkspaceGate>
        <p>编辑器已挂载</p>
      </WorkerWorkspaceGate>,
    );

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

  it("does not start a stale server acquire during StrictMode effect replay", async () => {
    const lockManager = new StrictModeWorkspaceLockManager();
    vi.stubGlobal("navigator", { locks: lockManager });
    let resolveFirst!: (response: Response) => void;
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(new Promise<Response>((resolve) => (resolveFirst = resolve)));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <StrictMode>
        <WorkerWorkspaceGate>
          <p>编辑器已挂载</p>
        </WorkerWorkspaceGate>
      </StrictMode>,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await act(async () => {
      resolveFirst(
        jsonResponse(
          { lease_expires_at: "2026-07-30T12:00:00Z", workspace_state: "editable" },
          201,
        ),
      );
    });
    expect(await screen.findByText("编辑器已挂载")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("aborts an in-flight server acquire when the gate unmounts", async () => {
    const lockManager = new FakeWorkspaceLockManager();
    vi.stubGlobal("navigator", { locks: lockManager });
    let resolveAcquire!: (response: Response) => void;
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(new Promise<Response>((resolve) => (resolveAcquire = resolve)));
    vi.stubGlobal("fetch", fetchMock);

    const view = render(<WorkerWorkspaceGate />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(request.signal?.aborted).toBe(false);

    view.unmount();
    expect(request.signal?.aborted).toBe(true);
    await act(async () => {
      resolveAcquire(jsonResponse({ error: { code: "stale_failure" } }, 500));
    });
  });

  it("keeps the loaded workspace mounted in offline mode when renewal loses the network", async () => {
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

    render(
      <WorkerWorkspaceGate>
        <p>编辑器已挂载</p>
      </WorkerWorkspaceGate>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("工作区可编辑。")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000);
    });

    expect(screen.queryByText("工作区可编辑。")).not.toBeInTheDocument();
    expect(screen.getByText("编辑器已挂载")).toBeInTheDocument();
    expect(screen.getByText("离线")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("PAP-AOF-SC-009 keeps a read-only recovery copy mounted after the lease is lost", async () => {
    vi.useFakeTimers();
    const lockManager = new FakeWorkspaceLockManager();
    vi.stubGlobal("navigator", { locks: lockManager });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          jsonResponse(
            { lease_expires_at: "2026-07-30T12:00:00Z", workspace_state: "editable" },
            201,
          ),
        )
        .mockResolvedValueOnce(jsonResponse({ error: { code: "workspace_lease_lost" } }, 409)),
    );

    render(
      <WorkerWorkspaceGate>
        {({ writable }) => <button disabled={!writable}>本地恢复内容</button>}
      </WorkerWorkspaceGate>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("工作区可编辑。")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000);
    });

    expect(screen.getByText("工作区已失效，已保留本地恢复副本。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "本地恢复内容" })).toBeDisabled();

    fireEvent(window, new Event("offline"));
    expect(screen.getByText("工作区已失效，已保留本地恢复副本。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "本地恢复内容" })).toBeDisabled();
  });
});
