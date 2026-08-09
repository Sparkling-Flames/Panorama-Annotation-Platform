import { describe, expect, it, vi } from "vitest";

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

class DelayedWorkspaceLockManager implements WorkspaceLockManager {
  private grantPending: (() => void) | undefined;
  private locked = false;

  request<T>(
    _name: string,
    _options: { ifAvailable: true; mode: "exclusive" },
    callback: (lock: Lock | null) => Promise<T> | T,
  ): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      this.grantPending = () => {
        this.locked = true;
        void Promise.resolve(callback({ name: "workspace", mode: "exclusive" } as Lock))
          .then(resolve, reject)
          .finally(() => {
            this.locked = false;
          });
      };
    });
  }

  grant(): void {
    this.grantPending?.();
    this.grantPending = undefined;
  }

  isLocked(): boolean {
    return this.locked;
  }
}

describe("WorkspaceTabCoordinator", () => {
  it("allows only one editable annotation tab and releases the browser lock", async () => {
    const lockManager = new FakeWorkspaceLockManager();
    const firstTab = new WorkspaceTabCoordinator(lockManager);
    const secondTab = new WorkspaceTabCoordinator(lockManager);

    await expect(firstTab.acquire()).resolves.toBe("editable");
    expect(lockManager.isLocked()).toBe(true);
    await expect(secondTab.acquire()).resolves.toBe("conflict");

    firstTab.release();
    await vi.waitFor(() => expect(lockManager.isLocked()).toBe(false));

    const nextTab = new WorkspaceTabCoordinator(lockManager);
    await expect(nextTab.acquire()).resolves.toBe("editable");
    nextTab.release();
  });

  it("releases a lock that arrives after the pending acquire was cancelled", async () => {
    const lockManager = new DelayedWorkspaceLockManager();
    const coordinator = new WorkspaceTabCoordinator(lockManager);

    const staleAcquire = coordinator.acquire();
    coordinator.release();
    lockManager.grant();

    await expect(staleAcquire).resolves.toBe("conflict");
    await vi.waitFor(() => expect(lockManager.isLocked()).toBe(false));
  });
});
