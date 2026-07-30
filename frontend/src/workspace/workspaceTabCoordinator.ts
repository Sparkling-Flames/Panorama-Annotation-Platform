export type WorkspaceTabState = "editable" | "conflict";

export interface WorkspaceLockManager {
  request<T>(
    name: string,
    options: { ifAvailable: true; mode: "exclusive" },
    callback: (lock: Lock | null) => Promise<T> | T,
  ): Promise<T>;
}

const WORKSPACE_LOCK_NAME = "panorama-annotation-edit-workspace";

export class WorkspaceTabCoordinator {
  private releaseCurrentLock: (() => void) | undefined;
  private lockRequest: Promise<void> | undefined;

  constructor(private readonly lockManager: WorkspaceLockManager = navigator.locks) {}

  acquire(): Promise<WorkspaceTabState> {
    if (this.releaseCurrentLock) {
      return Promise.resolve("editable");
    }

    return new Promise<WorkspaceTabState>((resolve, reject) => {
      this.lockRequest = this.lockManager
        .request<void>(
          WORKSPACE_LOCK_NAME,
          { ifAvailable: true, mode: "exclusive" },
          async (lock) => {
            if (lock === null) {
              resolve("conflict");
              return;
            }

            resolve("editable");
            await new Promise<void>((release) => {
              this.releaseCurrentLock = release;
            });
            this.releaseCurrentLock = undefined;
          },
        )
        .catch(reject);
    });
  }

  release(): void {
    this.releaseCurrentLock?.();
    this.releaseCurrentLock = undefined;
    void this.lockRequest;
  }
}
