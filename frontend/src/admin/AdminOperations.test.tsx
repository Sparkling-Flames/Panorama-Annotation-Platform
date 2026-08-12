import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdminOperations } from "./AdminOperations";

function jsonResponse(body: object): Response {
  return { json: async () => body, ok: true, status: 200 } as Response;
}

describe("AdminOperations", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  it("PAP-AAE-SC-001 PAP-AAE-SC-011 shows batch and worker operations", async () => {
    let operationsRead = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/admin/work-batches") {
        return jsonResponse({
          batches: [
            {
              batch_id: "batch-001",
              name: "POC batch",
              status: "open",
            },
          ],
        });
      }
      if (url === "/api/admin/work-batches/batch-001/operations") {
        operationsRead += 1;
        return jsonResponse({
          batch_id: "batch-001",
          counts: {
            active_workers: 2,
            block_reports: 1,
            deferred: 3,
            event_errors: 4,
            media_errors: 5,
            pending: 6,
            resolved: 7,
            revisions: 8,
            save_errors: 9,
            structure_blocks: 10,
            submitted: 11,
            unresolved: 12,
            verification_pending: 13,
          },
          max_event_delay_ms: 1500,
          refreshed_at: operationsRead === 1 ? "2026-08-11T01:02:03Z" : "2026-08-11T01:03:04Z",
          workers: [
            {
              accepted: 2,
              active_time_ms_by_rule: { "active-time-v1": 5000 },
              block_reports: 1,
              deferred: 3,
              rework_requests: 1,
              scope_observations: { annotatable: 4 },
              submitted: 4,
              worker_id: "worker-001",
            },
          ],
        });
      }
      if (url === "/api/admin/work-batches/batch-001/review-queue") {
        return jsonResponse({
          batch_id: "batch-001",
          items: [
            {
              conflict_summary: {
                geometry_state: "unresolved",
                portal_state: "resolved",
                reason_codes: ["geometry_multimodal"],
                scope_state: "unresolved",
              },
              input_revision_ids: ["revision-001", "revision-002"],
              input_sha256: "a".repeat(64),
              queue_type: "consensus_unresolved",
              rule_versions: {
                consensus_policy: "consensus-policy-v2",
                scope_policy: "scope-policy-v1",
              },
              task_id: "task-001",
              updated_at: "2026-08-11T01:02:03Z",
            },
          ],
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AdminOperations />);

    expect(
      await screen.findByRole("heading", { name: "批次运营概览 / Batch operations" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(document.querySelector('time[datetime="2026-08-11T01:02:03Z"]')).not.toBeNull(),
    );
    expect(screen.getByText("已提交任务 / Submitted tasks").nextElementSibling).toHaveTextContent(
      "11",
    );
    expect(screen.getByText("待处理任务 / Pending tasks").nextElementSibling).toHaveTextContent(
      "6",
    );
    expect(screen.getByText("保存错误 / Save errors").nextElementSibling).toHaveTextContent("9");
    expect(screen.getByText("worker-001")).toBeInTheDocument();
    expect(screen.getByText("5000 ms (active-time-v1)")).toBeInTheDocument();
    expect(
      screen.getByText("不计算工资或付款 / No wage or payment calculation"),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "复核队列 / Review queue" }),
    ).toBeInTheDocument();
    expect(screen.getByText("consensus_unresolved")).toBeInTheDocument();
    expect(screen.getByText("geometry_multimodal")).toBeInTheDocument();
    expect(screen.getByText("revision-001, revision-002")).toBeInTheDocument();
    expect(screen.queryByText(/tier|处罚|punishment/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷新 / Refresh" }));
    await waitFor(() =>
      expect(document.querySelector('time[datetime="2026-08-11T01:03:04Z"]')).not.toBeNull(),
    );
    expect(operationsRead).toBe(2);
  });

  it("shows an empty state without inventing a batch", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ batches: [] })));

    render(<AdminOperations />);

    expect(await screen.findByText("暂无批次 / No batches yet")).toBeInTheDocument();
  });

  it("PAP-AAE-SC-002 requests a provisional snapshot job and shows its frozen coverage result", async () => {
    document.cookie = "csrftoken=test-csrf; path=/";
    let requested = false;
    let snapshotRequests = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/admin/work-batches") {
        return jsonResponse({
          batches: [{ batch_id: "batch-002", name: "Snapshot batch", status: "open" }],
        });
      }
      if (url === "/api/admin/work-batches/batch-002/metric-snapshots") {
        expect(init?.method).toBe("POST");
        requested = true;
        snapshotRequests += 1;
        return {
          ...jsonResponse({
            input_count: 4,
            missing: snapshotRequests === 1 ? 0 : 1,
            not_evaluable: 0,
            provisional: true,
            result:
              snapshotRequests === 1
                ? {}
                : { missing: 1, not_evaluable: 0, support: 3, total_revisions: 4 },
            reused_from_snapshot_id: snapshotRequests === 1 ? null : "snapshot-001",
            snapshot_id: "snapshot-001",
            status: snapshotRequests === 1 ? "pending" : "succeeded",
            support: snapshotRequests === 1 ? 0 : 3,
          }),
          status: snapshotRequests === 1 ? 202 : 200,
        } as Response;
      }
      if (url === "/api/admin/work-batches/batch-002/operations") {
        return jsonResponse({
          batch_id: "batch-002",
          counts: {
            active_workers: 0,
            block_reports: 0,
            deferred: 0,
            event_errors: 0,
            media_errors: 0,
            pending: 0,
            resolved: 0,
            revisions: 4,
            save_errors: 0,
            structure_blocks: 0,
            submitted: 4,
            unresolved: 0,
            verification_pending: 1,
          },
          max_event_delay_ms: 0,
          workers: [],
          metric_snapshot: requested
            ? {
                input_count: 4,
                missing: 1,
                not_evaluable: 0,
                provisional: true,
                result: { missing: 1, not_evaluable: 0, support: 3, total_revisions: 4 },
                snapshot_id: "snapshot-001",
                status: "succeeded",
                support: 3,
              }
            : null,
          refreshed_at: "2026-08-11T02:00:00Z",
        });
      }
      if (url === "/api/admin/work-batches/batch-002/review-queue") {
        return jsonResponse({ batch_id: "batch-002", items: [] });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AdminOperations />);

    fireEvent.click(
      await screen.findByRole("button", { name: "计算当前情况 / Calculate current snapshot" }),
    );
    expect(await screen.findByText("pending")).toBeInTheDocument();
    expect(screen.getByText("临时快照 / Provisional snapshot")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新 / Refresh" }));
    expect(await screen.findByText("succeeded")).toBeInTheDocument();
    expect(screen.getByText("支持 / Support").nextElementSibling).toHaveTextContent("3");
    expect(screen.getByText("缺失 / Missing").nextElementSibling).toHaveTextContent("1");
    fireEvent.click(
      screen.getByRole("button", { name: "计算当前情况 / Calculate current snapshot" }),
    );
    expect(await screen.findByText("复用来源 / Reused from: snapshot-001")).toBeInTheDocument();
  });
});
