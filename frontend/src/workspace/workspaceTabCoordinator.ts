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
  private generation = 0;
  private releaseCurrentLock: (() => void) | undefined;
  private lockRequest: Promise<void> | undefined;

  constructor(private readonly lockManager: WorkspaceLockManager = navigator.locks) {}

  async acquire(): Promise<WorkspaceTabState> {
    if (this.releaseCurrentLock) {
      return "editable";
    }
    while (this.lockRequest !== undefined) {
      await this.lockRequest.catch(() => undefined);
      if (this.releaseCurrentLock) {
        return "editable";
      }
    }
    return this.requestLock();
  }

  private requestLock(): Promise<WorkspaceTabState> {
    const generation = ++this.generation;

    return new Promise<WorkspaceTabState>((resolve, reject) => {
      const lockRequest = this.lockManager
        .request<void>(
          WORKSPACE_LOCK_NAME,
          { ifAvailable: true, mode: "exclusive" },
          async (lock) => {
            if (lock === null || generation !== this.generation) {
              resolve("conflict");
              return;
            }

            resolve("editable");
            let releaseLock!: () => void;
            await new Promise<void>((release) => {
              releaseLock = release;
              if (generation === this.generation) {
                this.releaseCurrentLock = release;
              } else {
                release();
              }
            });
            if (this.releaseCurrentLock === releaseLock) {
              this.releaseCurrentLock = undefined;
            }
          },
        )
        .catch(reject)
        .finally(() => {
          if (this.lockRequest === lockRequest) {
            this.lockRequest = undefined;
          }
        });
      this.lockRequest = lockRequest;
    });
  }

  release(): void {
    this.generation += 1;
    const releaseLock = this.releaseCurrentLock;
    this.releaseCurrentLock = undefined;
    releaseLock?.();
    void this.lockRequest;
  }
}
