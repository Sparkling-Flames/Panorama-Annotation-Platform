import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnnotationState, MetaContract } from "../annotationState";
import { AssignmentWorkspace } from "./AssignmentWorkspace";

const offlineStore = vi.hoisted(() => ({
  drafts: [] as Array<Record<string, unknown>>,
  events: [] as Array<Record<string, unknown>>,
}));
vi.mock("../offline/recoveryStore", () => ({
  clearDraftRecovery: async (assignmentId: string) => {
    offlineStore.drafts = offlineStore.drafts.filter(
      (draft) => draft.assignment_id !== assignmentId,
    );
  },
  loadDraftRecovery: async (assignmentId: string) =>
    [...offlineStore.drafts].reverse().find((draft) => draft.assignment_id === assignmentId) ??
    null,
  queueActivityEvent: async (event: Record<string, unknown>) => {
    if (!offlineStore.events.some((item) => item.event_id === event.event_id)) {
      offlineStore.events.push(event);
    }
  },
  queuedActivityEvents: async (assignmentId: string) =>
    offlineStore.events.filter((event) => event.assignment_id === assignmentId),
  removeQueuedActivityEvent: async (eventId: string) => {
    offlineStore.events = offlineStore.events.filter((event) => event.event_id !== eventId);
  },
  saveDraftRecovery: async (draftRecovery: Record<string, unknown>) => {
    offlineStore.drafts.push(draftRecovery);
  },
}));

function jsonResponse(body: object, status = 200): Response {
  return { json: async () => body, ok: status >= 200 && status < 300, status } as Response;
}

const state: AnnotationState = {
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

function draft(
  currentState = state,
  draftVersion = 0,
  stateSha = "a".repeat(64),
  draftCycleId?: string,
) {
  return {
    ...(draftCycleId === undefined ? {} : { draft_cycle_id: draftCycleId }),
    draft_id: "draft-001",
    draft_version: draftVersion,
    state: currentState,
    state_sha: stateSha,
  };
}

const media = {
  assignment_id: "assignment-001",
  expires_at: new Date(Date.now() + 300_000).toISOString(),
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

const label = (zh: string, en: string) => ({ en, "zh-CN": zh });
const manualMetaContract: MetaContract = {
  copy_version: "annotation-meta-copy-v1",
  difficulty_options: [{ code: "trivial", label: label("非常简单", "Trivial / very easy") }],
  schema_version: "annotation-meta-v1",
  scope_options: [{ code: "annotatable", label: label("可标注", "Annotatable") }],
  scope_reason_options: [],
};
const semiMetaContract: MetaContract = {
  ...manualMetaContract,
  model_issue_options: [{ code: "corner_drift", label: label("角点错位或漂移", "Corner drift") }],
};

afterEach(() => {
  cleanup();
  offlineStore.drafts = [];
  offlineStore.events = [];
  document.cookie = "csrftoken=; Max-Age=0; Path=/";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("AssignmentWorkspace", () => {
  it("PAP-PAS-SC-002 keeps Semi initialization keyed away from the same browser's Manual state", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    const manualState: AnnotationState = {
      ...state,
      difficulty: [],
      geometry_attempt_status: null,
      pairs: [],
      seam_anchor_pair_id: null,
      worker_scope_observation: null,
    };
    const semiState: AnnotationState = {
      ...structuredClone(state),
      difficulty: [],
      model_issue: ["corner_drift"],
    };
    semiState.pairs[0].top.u = 0.33;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/media")) {
          return jsonResponse({
            ...media,
            assignment_id: url.includes("semi") ? "semi-assignment" : "manual-assignment",
          });
        }
        if (url.includes("/draft")) {
          const semi = url.includes("semi");
          return jsonResponse(
            draft(
              semi ? semiState : manualState,
              0,
              semi ? "b".repeat(64) : "a".repeat(64),
              semi ? "cycle-semi" : "cycle-manual",
            ),
          );
        }
        throw new Error(`unexpected ${url}`);
      }),
    );

    const common = { onSafeToSwitchChange: vi.fn(), tabId: "tab-001" };
    const view = render(
      <AssignmentWorkspace
        {...common}
        assignmentId="semi-assignment"
        key="semi-assignment"
        metaContract={semiMetaContract}
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    expect(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" })).toHaveValue(0.33);
    expect(screen.getByLabelText("角点错位或漂移")).toBeChecked();

    view.rerender(
      <AssignmentWorkspace
        {...common}
        assignmentId="manual-assignment"
        key="manual-assignment"
        metaContract={manualMetaContract}
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    await waitFor(() =>
      expect(
        screen.queryByRole("spinbutton", { name: "第 1 对顶点水平坐标" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.queryByLabelText("角点错位或漂移")).not.toBeInTheDocument();
    await waitFor(() => {
      const manualRecovery = offlineStore.drafts.find(
        (record) => record.assignment_id === "manual-assignment",
      );
      expect(manualRecovery?.draft_state).toEqual(manualState);
    });
  });

  it("PAP-MID-SC-012 blocks editing and submission when every media variant is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/media")) {
          return jsonResponse({ error: { code: "image_unavailable" } }, 409);
        }
        if (url.includes("/draft")) return jsonResponse(draft());
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={vi.fn()}
        tabId="tab-001"
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("image_unavailable");
    expect(screen.queryByLabelText("全景规范化坐标编辑区")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提交 Revision" })).not.toBeInTheDocument();
  });

  it("PAP-PAS-SC-007 reports a broken Semi prediction without mounting a Manual editor", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/draft")) {
          return jsonResponse({ error: { code: "semi_prediction_unavailable" } }, 409);
        }
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="broken-semi-assignment"
        onSafeToSwitchChange={vi.fn()}
        tabId="tab-001"
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "预测数据不可用，任务已技术阻断并等待管理员处理",
    );
    expect(screen.queryByLabelText("全景规范化坐标编辑区")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提交 Revision" })).not.toBeInTheDocument();
  });

  it("autosaves the real draft, overlays the real media, and submits only the server hash", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    const safe = vi.fn();
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/media")) return jsonResponse(media);
      if (url.includes("/draft") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as { state: AnnotationState };
        return jsonResponse(draft(body.state, 1, "b".repeat(64)));
      }
      if (url.includes("/draft")) return jsonResponse(draft());
      if (url.endsWith("/submit")) {
        return jsonResponse(
          {
            revision_id: "revision-001",
            revision_no: 1,
            state_sha: "b".repeat(64),
            submitted_at: "2026-08-09T12:00:00Z",
          },
          201,
        );
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        locale="en"
        onSafeToSwitchChange={safe}
        tabId="tab-001"
      />,
    );

    const preload = await screen.findByAltText("当前任务全景图（压缩）");
    fireEvent.load(preload);
    expect(await screen.findByLabelText("当前任务全景图标注底图")).toHaveAttribute(
      "href",
      media.variants[0].url,
    );
    expect(screen.getByText("本地 3D 预览（仅供参考）")).toBeInTheDocument();
    expect(screen.getByLabelText("本地 3D 预览（仅供参考）")).toHaveAttribute(
      "data-authority",
      "informational",
    );
    fireEvent.click(screen.getByRole("button", { name: "添加角点对" }));
    expect(screen.getByRole("button", { name: "提交 Revision" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "取消添加" }));
    expect(screen.getByRole("button", { name: "提交 Revision" })).toBeEnabled();

    fireEvent.change(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" }), {
      target: { value: "0.25" },
    });
    expect(await screen.findByText("未保存")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("已保存")).toBeInTheDocument(), { timeout: 2000 });
    expect(safe).toHaveBeenCalledWith(false);
    await waitFor(() => expect(safe).toHaveBeenLastCalledWith(true));

    fireEvent.click(screen.getByRole("button", { name: "提交 Revision" }));
    expect(await screen.findByText(/revision-001/)).toBeInTheDocument();
    const submitCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/submit"));
    const submitBody = JSON.parse(String((submitCall?.[1] as RequestInit).body)) as Record<
      string,
      unknown
    >;
    expect(submitBody).toMatchObject({
      client_build_sha: "development",
      expected_state_sha: "b".repeat(64),
      interaction_contract_version: "annotation-interaction-v1",
      locale: "en",
      tab_id: "tab-001",
      viewer_version: "annotation-viewer-v1",
    });
    expect(submitBody).not.toHaveProperty("state");
  });

  it("PAP-AOF-SC-008 syncs queued Draft and Activity data when the same baseline reconnects", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const activityBodies: Record<string, unknown>[] = [];
    const savedStates: AnnotationState[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/media")) return jsonResponse(media);
        if (url === "/api/worker/activity-events") {
          activityBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
          return jsonResponse({ event_id: activityBodies.at(-1)?.event_id }, 201);
        }
        if (url.includes("/draft") && init?.method === "PUT") {
          const body = JSON.parse(String(init.body)) as { state: AnnotationState };
          savedStates.push(body.state);
          return jsonResponse(draft(body.state, 1, "b".repeat(64)));
        }
        if (url.includes("/draft")) {
          return jsonResponse(draft(state, 0, "a".repeat(64), "cycle-offline"));
        }
        throw new Error(`unexpected ${url}`);
      }),
    );

    const props = {
      assignmentId: "assignment-001",
      onSafeToSwitchChange: vi.fn(),
      tabId: "tab-001",
    };
    const view = render(<AssignmentWorkspace {...props} />);
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    view.rerender(<AssignmentWorkspace {...props} online={false} />);
    fireEvent.change(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" }), {
      target: { value: "0.25" },
    });

    expect(screen.getByText("离线（本地保存）")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交 Revision" })).toBeDisabled();
    expect(savedStates).toEqual([]);
    await waitFor(() => expect(offlineStore.events).toHaveLength(1));

    view.rerender(<AssignmentWorkspace {...props} online />);
    await waitFor(() => expect(savedStates.at(-1)?.pairs[0].top.u).toBe(0.25));
    await waitFor(() =>
      expect(activityBodies.some((event) => event.interaction_type === "annotation_2d_edit")).toBe(
        true,
      ),
    );
    expect(offlineStore.events).toEqual([]);
    expect(screen.getByText("已保存")).toBeInTheDocument();
  });

  it("restores a persisted Draft and Undo stack when the server base is unchanged", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    const priorState = structuredClone(state);
    priorState.pairs[0].top.u = 0.321;
    const recoveredState = structuredClone(state);
    recoveredState.pairs[0].top.u = 0.654;
    offlineStore.drafts = [
      {
        assignment_id: "assignment-001",
        base_version: 0,
        draft_cycle_id: "cycle-recovery",
        draft_state: recoveredState,
        key: "assignment-001",
        pending_patch: { op: "replace", value: recoveredState },
        undo_stack: { future: [], past: [priorState] },
        updated_at: "2026-08-11T00:00:00Z",
      },
    ];
    const savedStates: AnnotationState[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/media")) return jsonResponse(media);
        if (url.includes("/draft") && init?.method === "PUT") {
          const body = JSON.parse(String(init.body)) as { state: AnnotationState };
          savedStates.push(body.state);
          return jsonResponse(draft(body.state, 1, "b".repeat(64), "cycle-recovery"));
        }
        if (url.includes("/draft")) {
          return jsonResponse(draft(state, 0, "a".repeat(64), "cycle-recovery"));
        }
        if (url.endsWith("/activity-events")) return jsonResponse({}, 201);
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={vi.fn()}
        tabId="tab-001"
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));

    const coordinate = await screen.findByRole("spinbutton", {
      name: "第 1 对顶点水平坐标",
    });
    expect(coordinate).toHaveValue(0.654);
    await waitFor(() => expect(savedStates.at(-1)?.pairs[0].top.u).toBe(0.654));
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(coordinate).toHaveValue(0.321);
  });

  it("keeps a persisted Draft read-only when the server base changed", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    const recoveredState = structuredClone(state);
    recoveredState.pairs[0].top.u = 0.654;
    offlineStore.drafts = [
      {
        assignment_id: "assignment-001",
        base_version: 0,
        draft_cycle_id: "cycle-recovery",
        draft_state: recoveredState,
        key: "assignment-001",
        pending_patch: { op: "replace", value: recoveredState },
        undo_stack: { future: [], past: [] },
        updated_at: "2026-08-11T00:00:00Z",
      },
    ];
    const draftWrites: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/media")) return jsonResponse(media);
        if (url.includes("/draft") && init?.method === "PUT") {
          draftWrites.push(init.body);
          return jsonResponse({}, 500);
        }
        if (url.includes("/draft")) {
          return jsonResponse({
            ...draft(state, 1, "b".repeat(64), "cycle-recovery"),
            updated_at: "2026-08-11T01:00:00Z",
          });
        }
        if (url.endsWith("/activity-events")) return jsonResponse({}, 201);
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={vi.fn()}
        tabId="tab-001"
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));

    const coordinate = await screen.findByRole("spinbutton", {
      name: "第 1 对顶点水平坐标",
    });
    expect(coordinate).toHaveValue(0.654);
    expect(coordinate).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("base_version 0");
    expect(screen.getByRole("alert")).toHaveTextContent("draft_version 1");
    expect(draftWrites).toEqual([]);
  });

  it("keeps a persisted recovery copy exportable when the server Draft is unavailable", async () => {
    const recoveredState = structuredClone(state);
    recoveredState.pairs[0].top.u = 0.654;
    offlineStore.drafts = [
      {
        assignment_id: "assignment-001",
        base_version: 3,
        draft_cycle_id: "cycle-recovery",
        draft_state: recoveredState,
        key: "assignment-001",
        pending_patch: { op: "replace", value: recoveredState },
        undo_stack: { future: [], past: [] },
        updated_at: "2026-08-11T00:00:00Z",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/draft")) {
          return jsonResponse({ error: { code: "batch_not_open" } }, 409);
        }
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={vi.fn()}
        tabId="tab-001"
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("服务器草稿当前不可读取");
    const recoveryLink = screen.getByRole("link", { name: "导出本地恢复副本" });
    const exported = decodeURIComponent(recoveryLink.getAttribute("href")?.split(",", 2)[1] ?? "");
    expect(JSON.parse(exported)).toMatchObject({
      assignment_id: "assignment-001",
      base_version: 3,
      draft_cycle_id: "cycle-recovery",
      draft_state: { pairs: [{ top: { u: 0.654 } }] },
    });
  });

  it("PAP-DRR-SC-002 keeps local state on a 409 and reloads only after the worker confirms", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    const safe = vi.fn();
    const serverState = structuredClone(state);
    serverState.pairs[0].top.u = 0.4;
    let draftReads = 0;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/media")) return jsonResponse(media);
      if (url.includes("/draft") && init?.method === "PUT") {
        return jsonResponse(
          {
            error: {
              code: "draft_conflict",
              server_draft_version: 1,
              server_updated_at: "2026-08-10T12:00:00Z",
            },
          },
          409,
        );
      }
      if (url.includes("/draft")) {
        draftReads += 1;
        return jsonResponse(draft(draftReads === 1 ? state : serverState, draftReads - 1));
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={safe}
        tabId="tab-001"
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    const coordinate = await screen.findByRole("spinbutton", {
      name: "第 1 对顶点水平坐标",
    });
    fireEvent.change(coordinate, { target: { value: "0.25" } });

    expect(await screen.findByRole("alert")).toHaveTextContent("草稿冲突");
    expect(coordinate).toHaveValue(0.25);
    expect(coordinate).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("base_version 0");
    expect(screen.getByRole("alert")).toHaveTextContent("draft_version 1");
    expect(document.querySelector('time[datetime="2026-08-10T12:00:00Z"]')).not.toBeNull();
    expect(screen.getByRole("button", { name: "提交 Revision" })).toBeDisabled();
    expect(safe).toHaveBeenLastCalledWith(false);

    fireEvent.click(screen.getByRole("button", { name: "重新加载服务器草稿" }));
    await waitFor(() => expect(draftReads).toBe(2));
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    await waitFor(() =>
      expect(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" })).toHaveValue(0.4),
    );
  });

  it("PAP-DRR-SC-004 keeps the idempotency key and blocks switching while a submit result is unknown", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    const safe = vi.fn();
    let submitCount = 0;
    const submitBodies: Record<string, unknown>[] = [];
    let rejectFirstSubmit!: (reason?: unknown) => void;
    const firstSubmit = new Promise<Response>((_, reject) => {
      rejectFirstSubmit = reject;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/media")) return jsonResponse(media);
        if (url.includes("/draft")) return jsonResponse(draft());
        if (url.endsWith("/submit")) {
          submitCount += 1;
          submitBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
          if (submitCount === 1) return firstSubmit;
          return jsonResponse(
            {
              revision_id: "revision-001",
              revision_no: 1,
              state_sha: "a".repeat(64),
              submitted_at: "2026-08-09T12:00:00Z",
            },
            200,
          );
        }
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={safe}
        tabId="tab-001"
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    const submitButton = await screen.findByRole("button", { name: "提交 Revision" });
    fireEvent.click(submitButton);
    await waitFor(() => expect(submitBodies).toHaveLength(1));
    expect(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" })).toBeDisabled();
    rejectFirstSubmit(new Error("response lost"));
    expect(await screen.findByRole("alert")).toHaveTextContent("提交结果未知");
    expect(safe).toHaveBeenLastCalledWith(false);
    expect(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "提交 Revision" }));
    await waitFor(() => expect(submitBodies).toHaveLength(2));
    expect(await screen.findByText(/revision-001/)).toBeInTheDocument();
    expect(submitBodies[1].idempotency_key).toBe(submitBodies[0].idempotency_key);
  });

  it("freezes the old editor when submission reports a lost workspace lease", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    const safe = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/media")) return jsonResponse(media);
        if (url.includes("/draft")) return jsonResponse(draft());
        if (url.endsWith("/submit")) {
          return jsonResponse({ error: { code: "workspace_lease_lost" } }, 409);
        }
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={safe}
        tabId="tab-001"
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    fireEvent.click(await screen.findByRole("button", { name: "提交 Revision" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("工作区已被接管");
    expect(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交 Revision" })).toBeDisabled();
    expect(safe).toHaveBeenLastCalledWith(false);
  });

  it("freezes the old editor when autosave reports a lost workspace lease", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    const safe = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/media")) return jsonResponse(media);
        if (url.includes("/draft") && init?.method === "PUT") {
          return jsonResponse({ error: { code: "workspace_lease_lost" } }, 409);
        }
        if (url.includes("/draft")) return jsonResponse(draft());
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={safe}
        tabId="tab-001"
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    fireEvent.change(await screen.findByRole("spinbutton", { name: "第 1 对顶点水平坐标" }), {
      target: { value: "0.25" },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("工作区已被接管");
    expect(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交 Revision" })).toBeDisabled();
    expect(safe).toHaveBeenLastCalledWith(false);
  });

  it("PAP-AOF-SC-009 preserves a read-only recovery copy when the batch is frozen", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    const safe = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/media")) return jsonResponse(media);
        if (url.includes("/draft") && init?.method === "PUT") {
          return jsonResponse({ error: { code: "batch_not_open" } }, 409);
        }
        if (url.includes("/draft")) {
          return jsonResponse(draft(state, 0, "a".repeat(64), "cycle-freeze"));
        }
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={safe}
        tabId="tab-001"
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    const coordinate = await screen.findByRole("spinbutton", {
      name: "第 1 对顶点水平坐标",
    });
    fireEvent.change(coordinate, { target: { value: "0.25" } });

    expect(await screen.findByRole("alert")).toHaveTextContent("batch_not_open");
    expect(screen.getByRole("alert")).toHaveTextContent("base_version 0");
    expect(coordinate).toHaveValue(0.25);
    expect(coordinate).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交 Revision" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "重试保存" })).not.toBeInTheDocument();
    const recoveryLink = screen.getByRole("link", { name: "导出本地恢复副本" });
    const exported = decodeURIComponent(recoveryLink.getAttribute("href")?.split(",", 2)[1] ?? "");
    expect(JSON.parse(exported)).toMatchObject({
      assignment_id: "assignment-001",
      base_version: 0,
      draft_cycle_id: "cycle-freeze",
      draft_state: { pairs: [{ top: { u: 0.25 } }] },
    });
    expect(exported).not.toContain("private.cos.test");
    expect(exported).not.toContain("workspace-test-token");
    expect(safe).toHaveBeenLastCalledWith(false);
    await waitFor(() =>
      expect((offlineStore.drafts.at(-1)?.draft_state as AnnotationState).pairs[0].top.u).toBe(
        0.25,
      ),
    );
  });

  it("PAP-PRV-SC-013 keeps submission available when local wireframe generation fails", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    vi.spyOn(crypto.subtle, "digest")
      .mockResolvedValueOnce(new Uint8Array(32).buffer)
      .mockRejectedValueOnce(new Error("preview digest failed"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/media")) return jsonResponse(media);
        if (url.includes("/draft")) return jsonResponse(draft());
        if (url.endsWith("/submit")) {
          return jsonResponse(
            {
              revision_id: "revision-preview-failed",
              revision_no: 1,
              state_sha: "a".repeat(64),
              submitted_at: "2026-08-10T00:00:00Z",
            },
            201,
          );
        }
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={vi.fn()}
        tabId="tab-001"
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    expect(await screen.findByRole("alert")).toHaveTextContent("预览生成失败");
    const submitButton = screen.getByRole("button", { name: "提交 Revision" });
    expect(submitButton).toBeEnabled();
    fireEvent.click(submitButton);
    expect(await screen.findByText(/revision-preview-failed/)).toBeInTheDocument();
  });

  it("keeps missing or DOM-forged preview state out of the submission contract", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    vi.spyOn(crypto.subtle, "digest").mockResolvedValue(new Uint8Array(32).buffer);
    const submitBodies: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/media")) return jsonResponse(media);
        if (url.includes("/draft")) return jsonResponse(draft());
        if (url.endsWith("/submit")) {
          submitBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
          return jsonResponse(
            {
              revision_id: "revision-preview-missing",
              revision_no: 1,
              state_sha: "a".repeat(64),
              submitted_at: "2026-08-10T00:00:00Z",
            },
            201,
          );
        }
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={vi.fn()}
        tabId="tab-001"
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    const preview = await screen.findByLabelText("本地 3D 预览（仅供参考）");
    expect(preview).toHaveAttribute("data-preview-status", "calculating");
    preview.setAttribute("data-authority", "forged-gating");
    fireEvent.click(screen.getByRole("button", { name: "提交 Revision" }));

    expect(await screen.findByText(/revision-preview-missing/)).toBeInTheDocument();
    expect(submitBodies).toHaveLength(1);
    expect(submitBodies[0]).not.toHaveProperty("preview");
    expect(submitBodies[0]).not.toHaveProperty("authority");
  });

  it("PAP-PRV-SC-015 locates a server validation error at its Portal", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    const portalId = "00000000-0000-4000-8000-000000000010";
    const portalState = structuredClone(state);
    portalState.portals = [
      {
        evidence_status: "direct_visible",
        geometry: {
          bottom_left: { u: 0.2, v: 0.8 },
          bottom_right: { u: 0.3, v: 0.8 },
          top_left: { u: 0.2, v: 0.3 },
          top_right: { u: 0.3, v: 0.3 },
        },
        host_edge_ref: state.pairs[0].pair_id,
        kind: "door",
        portal_id: portalId,
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/media")) return jsonResponse(media);
        if (url.includes("/draft") && init?.method === "PUT") {
          return jsonResponse(
            {
              error: {
                code: "annotation_portal_host_edge_invalid",
                field: "portals.host_edge_ref",
                portal_id: portalId,
                severity: "error",
              },
            },
            400,
          );
        }
        if (url.includes("/draft")) return jsonResponse(draft(portalState));
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        assignmentId="assignment-001"
        onSafeToSwitchChange={vi.fn()}
        tabId="tab-001"
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    fireEvent.change(await screen.findByRole("spinbutton", { name: "第 1 对顶点水平坐标" }), {
      target: { value: "0.25" },
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("annotation_portal_host_edge_invalid");
    expect(alert).toHaveTextContent("portals.host_edge_ref");
    expect(alert).toHaveTextContent(portalId);
    expect(screen.getByRole("link", { name: "定位 Portal" })).toHaveAttribute(
      "href",
      `#portal-${portalId}`,
    );
    expect(document.getElementById(`portal-${portalId}`)).not.toBeNull();
  });

  it("sends the existing coarse ActivityEvent envelope without input details", async () => {
    document.cookie = "csrftoken=workspace-test-token; Path=/";
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const activityBodies: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/media")) return jsonResponse(media);
        if (url === "/api/worker/activity-events") {
          activityBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
          return jsonResponse({ event_id: activityBodies.at(-1)?.event_id });
        }
        if (url.includes("/draft") && init?.method === "PUT") {
          const body = JSON.parse(String(init.body)) as { state: AnnotationState };
          return jsonResponse(draft(body.state, 1, "b".repeat(64), "cycle-001"));
        }
        if (url.includes("/draft")) {
          return jsonResponse(draft(state, 0, "a".repeat(64), "cycle-001"));
        }
        throw new Error(`unexpected ${url}`);
      }),
    );

    render(
      <AssignmentWorkspace
        activeTimeRuleVersion="frozen-test-rule"
        assignmentId="assignment-001"
        onSafeToSwitchChange={vi.fn()}
        tabId="tab-001"
      />,
    );
    fireEvent.load(await screen.findByAltText("当前任务全景图（压缩）"));
    fireEvent.change(await screen.findByRole("spinbutton", { name: "第 1 对顶点水平坐标" }), {
      target: { value: "0.25" },
    });
    await waitFor(() =>
      expect(activityBodies.some((body) => body.event_type === "interaction")).toBe(true),
    );
    const interaction = activityBodies.find((body) => body.event_type === "interaction");
    expect(interaction).toMatchObject({
      active_time_rule_version: "frozen-test-rule",
      assignment_id: "assignment-001",
      draft_cycle_id: "cycle-001",
      interaction_type: "annotation_2d_edit",
      tab_id: "tab-001",
    });
    expect(interaction).not.toHaveProperty("pointer_coordinates");
    expect(interaction).not.toHaveProperty("key_content");
    expect(interaction).not.toHaveProperty("active_seconds");
  });
});
