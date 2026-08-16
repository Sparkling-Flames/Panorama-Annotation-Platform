import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssignmentSwitcher } from "./AssignmentSwitcher";

function jsonResponse(body: object, status = 200): Response {
  return { json: async () => body, ok: status >= 200 && status < 300, status } as Response;
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

const first = {
  assignment_id: "assignment-001",
  batch_id: "batch-001",
  order_index: 0,
  queue_state: "ready",
  review_state: "unreviewed",
  task: { external_task_key: "warehouse-a", mode: "manual", task_id: "task-001" },
  work_state: "assigned",
};

const second = {
  assignment_id: "assignment-002",
  batch_id: "batch-001",
  order_index: 1,
  queue_state: "ready",
  review_state: "unreviewed",
  task: { external_task_key: "warehouse-b", mode: "manual", task_id: "task-002" },
  work_state: "assigned",
};

const savedState = {
  geometry_attempt_reason_text: "",
  geometry_attempt_status: "best_effort_complete",
  pairs: [
    {
      bottom: {
        point_id: "00000000-0000-4000-8000-000000000003",
        u: 0.12,
        v: 0.88,
      },
      order_index: 0,
      pair_id: "00000000-0000-4000-8000-000000000001",
      top: {
        point_id: "00000000-0000-4000-8000-000000000002",
        u: 0.1,
        v: 0.08,
      },
    },
  ],
  portals: [],
  seam_anchor_pair_id: "00000000-0000-4000-8000-000000000001",
  scope_reason_codes: [],
  scope_reason_text: "",
  worker_scope_observation: "annotatable",
};

const savedMedia = {
  assignment_id: first.assignment_id,
  expires_at: "2026-08-11T12:05:00+00:00",
  unavailable_roles: [],
  variants: [
    {
      coordinate_mapping: "normalized_identity",
      height: 1024,
      media_variant_id: "compressed-001",
      role: "compressed",
      url: "https://private.cos.test/compressed.jpg",
      width: 2048,
    },
  ],
};

describe("AssignmentSwitcher", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=assignment-test-token; Path=/";
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    cleanup();
    document.cookie = "csrftoken=; Max-Age=0; Path=/";
    localStorage.clear();
    sessionStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("PAP-PAS-SC-001 keeps Manual assignment state and browser storage prediction-free", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          jsonResponse({ batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }] }),
        )
        .mockResolvedValueOnce(jsonResponse({ assignments: [first] })),
    );

    render(<AssignmentSwitcher tabId="tab-001" />);

    expect(await screen.findByText("warehouse-a")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("界面语言"), { target: { value: "en" } });
    expect(screen.getByLabelText("Interface language")).toHaveValue("en");
    expect(document.body).not.toHaveTextContent(/prediction|model_issue|model risk|assist/i);
    expect(localStorage).toHaveLength(0);
    expect(sessionStorage).toHaveLength(0);
  });

  it("uses English copy for Assignment loading failures and retry after the worker selects English", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }] }),
      )
      .mockResolvedValueOnce(jsonResponse({ error: { code: "assignment_list_unavailable" } }, 503))
      .mockResolvedValueOnce(
        jsonResponse({ batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }] }),
      )
      .mockResolvedValueOnce(jsonResponse({ assignments: [first] }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取或更新 Assignment");
    fireEvent.change(screen.getByLabelText("界面语言"), { target: { value: "en" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Unable to load or update assignments.");
    fireEvent.click(screen.getByRole("button", { name: "Retry loading" }));
    expect(await screen.findByText("warehouse-a")).toBeInTheDocument();
    expect(screen.getByText("Queue: Ready")).toBeInTheDocument();
    expect(screen.getByText("Work: Assigned")).toBeInTheDocument();
  });

  it("PAP-TBA-SC-006 skips temporarily and opens the next ordered Assignment", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }] }),
      )
      .mockResolvedValueOnce(jsonResponse({ assignments: [first, second] }))
      .mockResolvedValueOnce(jsonResponse({ ...first, queue_state: "deferred" }))
      .mockResolvedValueOnce(jsonResponse({ ...second, work_state: "in_progress" }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    expect(await screen.findByText("warehouse-a")).toBeInTheDocument();
    expect(screen.getByText("warehouse-b")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "暂缓 warehouse-a" }));
    expect(await screen.findByText("当前 Assignment：warehouse-b")).toBeInTheDocument();
    expect(screen.getByText("工作：in_progress")).toBeInTheDocument();

    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/worker/assignments/assignment-001/queue-state",
      expect.objectContaining({
        body: JSON.stringify({ queue_state: "deferred", tab_id: "tab-001" }),
        method: "POST",
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "/api/worker/assignments/assignment-002/open",
      expect.objectContaining({
        body: JSON.stringify({ tab_id: "tab-001" }),
        method: "POST",
      }),
    );
  });

  it("PAP-TBA-SC-007 returns to the earliest deferred Assignment at the end", async () => {
    const deferredFirst = { ...first, queue_state: "deferred" };
    const submittedSecond = { ...second, work_state: "submitted" };
    const third = {
      ...second,
      assignment_id: "assignment-003",
      order_index: 2,
      task: { ...second.task, external_task_key: "warehouse-c", task_id: "task-003" },
      work_state: "in_progress",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }] }),
      )
      .mockResolvedValueOnce(jsonResponse({ assignments: [deferredFirst, submittedSecond, third] }))
      .mockResolvedValueOnce(jsonResponse({ ...third, queue_state: "deferred" }))
      .mockResolvedValueOnce(
        jsonResponse({ ...deferredFirst, queue_state: "ready", work_state: "in_progress" }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    fireEvent.click(await screen.findByRole("button", { name: "暂缓 warehouse-c" }));
    expect(await screen.findByText("当前 Assignment：warehouse-a")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /稍后复访/ })).not.toBeInTheDocument();

    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "/api/worker/assignments/assignment-001/open",
      expect.objectContaining({
        body: JSON.stringify({ tab_id: "tab-001" }),
        method: "POST",
      }),
    );
  });

  it("PAP-TBA-SC-013 reports a real block instead of treating Skip as terminal", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }] }),
      )
      .mockResolvedValueOnce(jsonResponse({ assignments: [first] }))
      .mockResolvedValueOnce(jsonResponse({ ...first, work_state: "blocked" }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    fireEvent.click(await screen.findByRole("button", { name: "报告阻断 warehouse-a" }));
    fireEvent.change(screen.getByLabelText("阻断原因"), {
      target: { value: "technical_failure" },
    });
    fireEvent.change(screen.getByLabelText("说明"), {
      target: { value: "COS image could not be decoded." },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认报告阻断" }));

    await waitFor(() => expect(screen.getByText("工作：blocked")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/worker/assignments/assignment-001/block",
      expect.objectContaining({
        body: JSON.stringify({
          reason_code: "technical_failure",
          reason_text: "COS image could not be decoded.",
          tab_id: "tab-001",
        }),
        method: "POST",
      }),
    );
  });

  it("starts a new revision workspace for a submitted Assignment", async () => {
    const submitted = { ...first, work_state: "submitted" };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/worker/batches") {
        return jsonResponse({
          batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }],
        });
      }
      if (url === "/api/worker/batches/batch-001/assignments") {
        return jsonResponse({ assignments: [submitted] });
      }
      if (url === "/api/worker/assignments/assignment-001/revise") {
        return jsonResponse({ ...submitted, work_state: "in_progress" }, 201);
      }
      if (url.endsWith("/media")) {
        return jsonResponse({ assignment_id: "assignment-001", variants: [] });
      }
      if (url.includes("/draft")) throw new Error("draft load stopped for focused test");
      throw new Error(`unexpected ${url} ${init?.method ?? "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    fireEvent.click(await screen.findByRole("button", { name: "修订 warehouse-a" }));
    expect(await screen.findByText("当前 Assignment：warehouse-a")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/worker/assignments/assignment-001/revise",
      expect.objectContaining({ body: JSON.stringify({ tab_id: "tab-001" }), method: "POST" }),
    );
  });

  it("shows only server-provided rework guidance and opens its Draft", async () => {
    const reviewed = {
      ...first,
      queue_state: "needs_revisit",
      review_state: "changes_requested",
      work_state: "submitted",
    };
    const rework = {
      assignment_id: first.assignment_id,
      due_at: "2026-08-11T12:00:00+00:00",
      instruction: "Please submit an annotatable revision.",
      request_id: "rework-001",
      status: "pending",
    };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/worker/batches") {
        return jsonResponse({
          batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }],
        });
      }
      if (url === "/api/worker/batches/batch-001/assignments") {
        return jsonResponse({ assignments: [reviewed] });
      }
      if (url === "/api/worker/rework-requests") {
        return jsonResponse({ requests: [rework] });
      }
      if (url === "/api/worker/rework-requests/rework-001/accept") {
        return jsonResponse({ ...rework, status: "in_progress" }, 201);
      }
      if (url.endsWith("/media")) {
        return jsonResponse({ assignment_id: first.assignment_id, variants: [] });
      }
      if (url.includes("/draft")) throw new Error("draft load stopped for focused test");
      throw new Error(`unexpected ${url} ${init?.method ?? "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    expect(await screen.findByText(rework.instruction)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/source_revision|portals|geometry/);
    expect(screen.getByRole("button", { name: "修订 warehouse-a" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "开始返工 / Start rework" }));
    expect(await screen.findByText("当前 Assignment：warehouse-a")).toBeInTheDocument();
    expect(screen.getByText("状态 / Status: 返工中 / In progress")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/worker/rework-requests/rework-001/accept",
      expect.objectContaining({ body: JSON.stringify({ tab_id: "tab-001" }), method: "POST" }),
    );
  });

  it("PAP-DRR-SC-017 PAP-DRR-SC-018 refreshes new rework on focus without accepting stale reads", async () => {
    const submitted = { ...first, work_state: "submitted" };
    const reviewed = {
      ...submitted,
      queue_state: "needs_revisit",
      review_state: "changes_requested",
    };
    const rework = {
      assignment_id: first.assignment_id,
      due_at: "2026-08-11T12:00:00+00:00",
      instruction: "Use your own evidence to submit an annotatable revision.",
      request_id: "rework-focus-001",
      status: "pending",
    };
    const foregroundReads: Array<{
      payload: ReturnType<typeof deferred<{ assignments: object[] }>>;
      signal: AbortSignal | null | undefined;
    }> = [];
    let assignmentReadCount = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/worker/batches") {
        return Promise.resolve(
          jsonResponse({ batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }] }),
        );
      }
      if (url === "/api/worker/batches/batch-001/assignments") {
        assignmentReadCount += 1;
        if (assignmentReadCount === 1) {
          return Promise.resolve(jsonResponse({ assignments: [submitted] }));
        }
        const payload = deferred<{ assignments: object[] }>();
        foregroundReads.push({ payload, signal: init?.signal });
        return Promise.resolve({ json: () => payload.promise, ok: true, status: 200 } as Response);
      }
      if (url === "/api/worker/rework-requests") {
        return Promise.resolve(jsonResponse({ requests: [rework] }));
      }
      throw new Error(`unexpected ${url} ${init?.method ?? "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    expect(await screen.findByText("warehouse-a")).toBeInTheDocument();
    expect(screen.queryByText(rework.instruction)).not.toBeInTheDocument();

    fireEvent(window, new Event("focus"));
    await waitFor(() => expect(foregroundReads).toHaveLength(1));
    fireEvent(window, new Event("focus"));
    await waitFor(() => expect(foregroundReads).toHaveLength(2));
    expect(foregroundReads[0].signal?.aborted).toBe(true);

    foregroundReads[1].payload.resolve({ assignments: [reviewed] });
    expect(await screen.findByText(rework.instruction)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始返工 / Start rework" })).toBeInTheDocument();

    await act(async () => {
      foregroundReads[0].payload.resolve({ assignments: [submitted] });
      await Promise.resolve();
    });

    expect(screen.getByText(rework.instruction)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始返工 / Start rework" })).toBeInTheDocument();
    const requestedUrls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(
      requestedUrls.some(
        (url) => url.includes("/draft") || url.endsWith("/media") || url.includes("/revisions"),
      ),
    ).toBe(false);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method !== undefined)).toBe(false);
  });

  it("PAP-DRR-SC-018 rejects an initial Assignment response that arrives after focus revalidation", async () => {
    const submitted = { ...first, work_state: "submitted" };
    const reviewed = {
      ...submitted,
      queue_state: "needs_revisit",
      review_state: "changes_requested",
    };
    const rework = {
      assignment_id: first.assignment_id,
      due_at: "2026-08-11T12:00:00+00:00",
      instruction: "The newest review instruction must remain visible.",
      request_id: "rework-generation-001",
      status: "pending",
    };
    const assignmentReads: Array<ReturnType<typeof deferred<{ assignments: object[] }>>> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/worker/batches") {
        return Promise.resolve(
          jsonResponse({ batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }] }),
        );
      }
      if (url === "/api/worker/batches/batch-001/assignments") {
        const payload = deferred<{ assignments: object[] }>();
        assignmentReads.push(payload);
        return Promise.resolve({ json: () => payload.promise, ok: true, status: 200 } as Response);
      }
      if (url === "/api/worker/rework-requests") {
        return Promise.resolve(jsonResponse({ requests: [rework] }));
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    await waitFor(() => expect(assignmentReads).toHaveLength(1));
    fireEvent(window, new Event("focus"));
    await waitFor(() => expect(assignmentReads).toHaveLength(2));

    assignmentReads[1].resolve({ assignments: [reviewed] });
    expect(await screen.findByText(rework.instruction)).toBeInTheDocument();
    await act(async () => {
      assignmentReads[0].resolve({ assignments: [submitted] });
      await Promise.resolve();
    });

    expect(screen.getByText(rework.instruction)).toBeInTheDocument();
    expect(screen.queryByText("正在读取 Assignment…")).not.toBeInTheDocument();
  });

  it("PAP-DRR-SC-018 rejects a cancelled ReworkRequest response after feedback disappears", async () => {
    const reviewed = {
      ...first,
      queue_state: "needs_revisit",
      review_state: "changes_requested",
      work_state: "submitted",
    };
    const submitted = { ...first, work_state: "submitted" };
    const rework = {
      assignment_id: first.assignment_id,
      due_at: "2026-08-11T12:00:00+00:00",
      instruction: "This cancelled response must not appear.",
      request_id: "rework-stale-001",
      status: "pending",
    };
    const staleRework = deferred<{ requests: object[] }>();
    let staleReworkSignal: AbortSignal | null | undefined;
    let assignmentReadCount = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/worker/batches") {
        return Promise.resolve(
          jsonResponse({ batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }] }),
        );
      }
      if (url === "/api/worker/batches/batch-001/assignments") {
        assignmentReadCount += 1;
        return Promise.resolve(
          jsonResponse({ assignments: [assignmentReadCount === 1 ? reviewed : submitted] }),
        );
      }
      if (url === "/api/worker/rework-requests") {
        staleReworkSignal = init?.signal;
        return Promise.resolve({
          json: () => staleRework.promise,
          ok: true,
          status: 200,
        } as Response);
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    await waitFor(() => expect(staleReworkSignal).toBeDefined());
    fireEvent(window, new Event("focus"));
    await waitFor(() => expect(assignmentReadCount).toBe(2));
    await waitFor(() => expect(staleReworkSignal?.aborted).toBe(true));
    await act(async () => {
      staleRework.resolve({ requests: [rework] });
      await Promise.resolve();
    });

    expect(screen.queryByText(rework.instruction)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "开始返工 / Start rework" }),
    ).not.toBeInTheDocument();
  });

  it("PAP-DRR-SC-018 rejects a pending focus response after explicit list retry", async () => {
    const reviewed = {
      ...first,
      queue_state: "needs_revisit",
      review_state: "changes_requested",
      work_state: "submitted",
    };
    const staleSubmitted = { ...first, work_state: "submitted" };
    const rework = {
      assignment_id: first.assignment_id,
      due_at: "2026-08-11T12:00:00+00:00",
      instruction: "The explicit retry result must remain visible.",
      request_id: "rework-retry-001",
      status: "pending",
    };
    const foregroundRead = deferred<{ assignments: object[] }>();
    const initialReworkFailure = deferred<Response>();
    let foregroundSignal: AbortSignal | null | undefined;
    let assignmentReadCount = 0;
    let reworkReadCount = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/worker/batches") {
        return Promise.resolve(
          jsonResponse({ batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }] }),
        );
      }
      if (url === "/api/worker/batches/batch-001/assignments") {
        assignmentReadCount += 1;
        if (assignmentReadCount === 2) {
          foregroundSignal = init?.signal;
          return Promise.resolve({
            json: () => foregroundRead.promise,
            ok: true,
            status: 200,
          } as Response);
        }
        return Promise.resolve(jsonResponse({ assignments: [reviewed] }));
      }
      if (url === "/api/worker/rework-requests") {
        reworkReadCount += 1;
        return reworkReadCount === 1
          ? initialReworkFailure.promise
          : Promise.resolve(jsonResponse({ requests: [rework] }));
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    await waitFor(() => expect(reworkReadCount).toBe(1));
    fireEvent(window, new Event("focus"));
    await waitFor(() => expect(foregroundSignal).toBeDefined());
    initialReworkFailure.resolve(jsonResponse({ error: { code: "temporarily_unavailable" } }, 503));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取或更新 Assignment");
    fireEvent.click(screen.getByRole("button", { name: "重试读取" }));

    await waitFor(() => expect(foregroundSignal?.aborted).toBe(true));
    expect(await screen.findByText(rework.instruction)).toBeInTheDocument();
    await act(async () => {
      foregroundRead.resolve({ assignments: [staleSubmitted] });
      await Promise.resolve();
    });

    expect(screen.getByText(rework.instruction)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始返工 / Start rework" })).toBeInTheDocument();
  });

  it("PAP-DRR-SC-019 leaves an open Assignment editor untouched on focus", async () => {
    const editable = {
      ...first,
      task: { ...first.task, active_time_rule_version: "active-time-v1" },
    };
    let assignmentReadCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/worker/batches") {
        return jsonResponse({
          batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }],
        });
      }
      if (url === "/api/worker/batches/batch-001/assignments") {
        assignmentReadCount += 1;
        return jsonResponse({ assignments: [editable] });
      }
      if (url === "/api/worker/assignments/assignment-001/open") {
        return jsonResponse({ ...editable, work_state: "in_progress" });
      }
      if (url.endsWith("/media")) {
        return jsonResponse(savedMedia);
      }
      if (url.includes("/draft")) {
        return jsonResponse({
          draft_cycle_id: "cycle-focus-001",
          draft_id: "draft-focus-001",
          draft_version: 0,
          state: savedState,
          state_sha: "a".repeat(64),
        });
      }
      throw new Error(`unexpected ${url} ${init?.method ?? "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    fireEvent.click(await screen.findByRole("button", { name: "打开 warehouse-a" }));
    expect(await screen.findByText("当前 Assignment：warehouse-a")).toBeInTheDocument();
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    expect(await screen.findByText("已保存")).toBeInTheDocument();
    expect(screen.getByLabelText("界面语言")).toBeEnabled();
    expect(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" })).toHaveValue(0.1);

    await act(async () => {
      fireEvent(window, new Event("focus"));
      await Promise.resolve();
    });

    expect(assignmentReadCount).toBe(1);
    expect(screen.getByText("当前 Assignment：warehouse-a")).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" })).toHaveValue(0.1);
  });
});
