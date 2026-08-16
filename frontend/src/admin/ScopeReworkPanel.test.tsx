import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScopeReworkPanel } from "./ScopeReworkPanel";

function jsonResponse(body: object, status = 200): Response {
  return { json: async () => body, ok: status >= 200 && status < 300, status } as Response;
}

describe("ScopeReworkPanel", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  it("PAP-DRR-SC-016 creates a scope-only rework without sending geometry", async () => {
    document.cookie = "csrftoken=test-csrf; path=/";
    const dueAt = new Date("2026-08-20T12:30").toISOString();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/admin/revisions/revision-001") {
        return jsonResponse({
          feedback_exposed: false,
          revision_id: "revision-001",
          state: {
            geometry_attempt_status: "partial",
            pairs: [{ pair_id: "pair-private", top: { u: 0.2 }, bottom: { u: 0.3 } }],
            portals: [{ kind: "door" }],
            scope_reason_codes: ["insufficient_evidence"],
            worker_scope_observation: "representation_oos",
          },
          state_sha: "a".repeat(64),
          worker_id: "worker-001",
        });
      }
      if (url === "/api/admin/revisions/revision-001/scope-rework") {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({
          due_at: dueAt,
          instruction:
            "最终范围已裁定为可标注。请只依据你自己的观察返工，不要复制其他工人的几何或 Portal。 / Final scope is annotatable. Rework only from your own observations. Do not copy another worker's geometry or portals.",
          reason: "Scope evidence was reviewed and the task is annotatable.",
        });
        expect(String(init?.body)).not.toContain("pair-private");
        expect(String(init?.body)).not.toContain("door");
        return jsonResponse(
          {
            adjudication_id: "adjudication-001",
            assignment_id: "assignment-001",
            due_at: "2026-08-20T02:30:00Z",
            instruction: "safe instruction",
            request_id: "request-001",
            review_id: "review-001",
            status: "pending",
          },
          201,
        );
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ScopeReworkPanel revisionId="revision-001" />);

    fireEvent.click(screen.getByRole("button", { name: "复核 Scope / Review scope" }));
    expect(await screen.findByText("representation_oos")).toBeInTheDocument();
    expect(screen.getByText("证据摘要 / Evidence summary").nextElementSibling).toHaveTextContent(
      "1 对 / pairs · 1 个 / portals",
    );
    expect(screen.queryByText("pair-private")).not.toBeInTheDocument();
    expect(screen.queryByText("door")).not.toBeInTheDocument();
    expect(screen.getByText(/不得粘贴其他工人的 geometry 或 portal/i)).toBeInTheDocument();
    expect(screen.getByText(/不会选择或替换 Task 的正式交付结果/i)).toBeInTheDocument();
    expect(screen.getByText("范围观察 / Scope observation")).toBeInTheDocument();
    expect(screen.getByText("几何完成度 / Geometry attempt")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("裁定原因 / Decision reason"), {
      target: { value: "Scope evidence was reviewed and the task is annotatable." },
    });
    fireEvent.change(screen.getByLabelText("截止时间 / Due time"), {
      target: { value: "2026-08-20T12:30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建 Scope 返工 / Create rework" }));

    expect(await screen.findByText("Scope 返工已创建 / Scope rework created")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});
