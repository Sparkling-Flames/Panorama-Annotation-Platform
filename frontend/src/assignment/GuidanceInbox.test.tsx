import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { GuidanceInbox } from "./GuidanceInbox";

function jsonResponse(body: object, status = 200): Response {
  return { json: async () => body, ok: status >= 200 && status < 300, status } as Response;
}

afterEach(() => {
  cleanup();
  document.cookie = "csrftoken=; Max-Age=0; Path=/";
  vi.restoreAllMocks();
});

it("PAP-AAE-SC-007 shows a private guidance summary and only sends acknowledgement", async () => {
  document.cookie = "csrftoken=guidance-test-token; Path=/";
  const event = {
    acknowledged_at: null,
    administrator_id: "admin-001",
    assignment_id: "assignment-001",
    batch_id: "batch-001",
    category: "geometry_hint",
    channel: "wechat",
    created_at: "2026-08-11T10:00:00Z",
    feedback_draft_cycle_id: null,
    guidance_id: "guidance-001",
    revision_id: null,
    summary: "请重新检查门洞四角。",
    task_id: "task-001",
    worker_id: "worker-001",
  };
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse({ events: [event] }))
    .mockResolvedValueOnce(jsonResponse({ ...event, acknowledged_at: "2026-08-11T10:05:00Z" }));
  vi.stubGlobal("fetch", fetchMock);

  render(<GuidanceInbox />);

  fireEvent.click(screen.getByRole("button", { name: "查看指导 / View guidance" }));
  expect(await screen.findByText(event.summary)).toBeInTheDocument();
  expect(screen.getByText("渠道 / Channel: wechat")).toBeInTheDocument();
  expect(screen.getByText("管理员 / Administrator: admin-001")).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /回复|reply/i })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "确认已阅读 / Acknowledge" }));
  expect(await screen.findByText("已确认 / Acknowledged")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/worker/guidance-events/guidance-001/acknowledge",
    expect.objectContaining({ body: "{}", method: "POST" }),
  );
});
