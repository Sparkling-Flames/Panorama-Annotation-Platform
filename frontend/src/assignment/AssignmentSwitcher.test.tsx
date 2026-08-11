import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssignmentSwitcher } from "./AssignmentSwitcher";

function jsonResponse(body: object, status = 200): Response {
  return { json: async () => body, ok: status >= 200 && status < 300, status } as Response;
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
});
