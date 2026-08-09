import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssignmentSwitcher } from "./AssignmentSwitcher";

function jsonResponse(body: object, status = 200): Response {
  return { json: async () => body, ok: status >= 200 && status < 300, status } as Response;
}

const first = {
  assignment_id: "assignment-001",
  batch_id: "batch-001",
  queue_state: "ready",
  review_state: "unreviewed",
  task: { external_task_key: "warehouse-a", mode: "manual", task_id: "task-001" },
  work_state: "assigned",
};

const second = {
  assignment_id: "assignment-002",
  batch_id: "batch-001",
  queue_state: "ready",
  review_state: "unreviewed",
  task: { external_task_key: "warehouse-b", mode: "manual", task_id: "task-002" },
  work_state: "assigned",
};

describe("AssignmentSwitcher", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=assignment-test-token; Path=/";
  });

  afterEach(() => {
    cleanup();
    document.cookie = "csrftoken=; Max-Age=0; Path=/";
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("task 4.4 defers a difficult item without blocking another Assignment", async () => {
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
    await waitFor(() => expect(screen.getByText("队列：deferred")).toBeInTheDocument());
    expect(screen.getByText("warehouse-b")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "打开 warehouse-b" }));
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

  it("task 4.4 moves an Assignment through needs_revisit and back to ready", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ batches: [{ batch_id: "batch-001", name: "Warehouse", status: "open" }] }),
      )
      .mockResolvedValueOnce(jsonResponse({ assignments: [first] }))
      .mockResolvedValueOnce(jsonResponse({ ...first, queue_state: "needs_revisit" }))
      .mockResolvedValueOnce(jsonResponse(first));
    vi.stubGlobal("fetch", fetchMock);

    render(<AssignmentSwitcher tabId="tab-001" />);

    fireEvent.click(await screen.findByRole("button", { name: "稍后复访 warehouse-a" }));
    await waitFor(() => expect(screen.getByText("队列：needs_revisit")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "设为就绪 warehouse-a" }));
    await waitFor(() => expect(screen.getByText("队列：ready")).toBeInTheDocument());

    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "/api/worker/assignments/assignment-001/queue-state",
      expect.objectContaining({
        body: JSON.stringify({ queue_state: "ready", tab_id: "tab-001" }),
        method: "POST",
      }),
    );
  });
});
