import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DataNoticeGate } from "./DataNoticeGate";

const notice = {
  accepted: false,
  accepted_at: null,
  collected_data: ["account_and_worker_id", "active_time_coarse_events"],
  copy: {
    en: {
      collected_data: ["Account and worker ID", "Coarse active-time events"],
      summary: "We collect only the listed platform records.",
      title: "Data notice",
    },
    "zh-CN": {
      collected_data: ["账号与工人 ID", "粗粒度活动时间事件"],
      summary: "我们仅收集下列平台记录。",
      title: "数据告知",
    },
  },
  notice_version: "data-notice-v1",
};

function jsonResponse(body: object, status = 200): Response {
  return { json: async () => body, ok: status >= 200 && status < 300, status } as Response;
}

describe("DataNoticeGate", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("PAP-PBD-SC-004 keeps the workspace unmounted until the current notice is accepted", async () => {
    document.cookie = "csrftoken=privacy-test-token; Path=/";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(notice))
      .mockResolvedValueOnce(
        jsonResponse(
          { accepted_at: "2026-08-11T01:02:03Z", notice_version: notice.notice_version },
          201,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <DataNoticeGate>
        <p>workspace</p>
      </DataNoticeGate>,
    );

    expect(
      await screen.findByRole("heading", { name: "数据告知 / Data notice" }),
    ).toBeInTheDocument();
    expect(screen.getByText(notice.copy["zh-CN"].summary)).toBeInTheDocument();
    expect(screen.getByText(notice.copy.en.summary)).toBeInTheDocument();
    expect(screen.queryByText("workspace")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认并继续 / Accept and continue" }));

    expect(await screen.findByText("workspace")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/privacy/notice/accept",
      expect.objectContaining({
        body: JSON.stringify({ notice_version: notice.notice_version }),
        method: "POST",
      }),
    );
  });

  it("mounts the workspace when the current notice was already accepted", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ ...notice, accepted: true })));
    render(
      <DataNoticeGate>
        <p>workspace</p>
      </DataNoticeGate>,
    );
    expect(await screen.findByText("workspace")).toBeInTheDocument();
  });

  it("requires reconfirmation when focus reveals a new notice version", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse({ ...notice, accepted: true }))
        .mockResolvedValueOnce(
          jsonResponse({ ...notice, accepted: false, notice_version: "data-notice-v2" }),
        ),
    );
    render(
      <DataNoticeGate>
        <p>workspace</p>
      </DataNoticeGate>,
    );
    expect(await screen.findByText("workspace")).toBeInTheDocument();

    window.dispatchEvent(new Event("focus"));

    await waitFor(() => expect(screen.queryByText("workspace")).not.toBeInTheDocument());
    expect(screen.getByText("notice_version: data-notice-v2")).toBeInTheDocument();
  });
});
