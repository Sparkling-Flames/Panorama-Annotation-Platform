import { useCallback, useEffect, useRef, useState } from "react";

import { AnnotationEditor, type AnnotationHistory } from "../annotation/AnnotationEditor";
import {
  type AnnotationState,
  type MetaContract,
  annotationStateSha,
  annotationStateSubmissionReady,
} from "../annotationState";
import { apiFetch } from "../api";
import { ACTIVE_TIME_RULE_VERSION, ActivityTracker } from "../activity/activityTracker";
import { AssignmentMedia } from "../media/AssignmentMedia";
import { formatLocalTimestamp, type SupportedLocale } from "../locale";
import { CLIENT_BUILD_SHA, INTERACTION_CONTRACT_VERSION, VIEWER_VERSION } from "../versioning";
import {
  type DraftRecoveryRecord,
  type QueuedActivityEvent,
  clearDraftRecovery,
  loadDraftRecovery,
  queueActivityEvent,
  queuedActivityEvents,
  removeQueuedActivityEvent,
  saveDraftRecovery,
} from "../offline/recoveryStore";

type DraftPayload = {
  draft_cycle_id: string;
  draft_id: string;
  draft_version: number;
  state: AnnotationState;
  state_sha: string;
  updated_at?: string;
};

type RevisionPayload = {
  revision_id: string;
  revision_no: number;
  state_sha: string;
  submitted_at: string;
};

type SaveStatus = "conflict" | "error" | "offline" | "saved" | "saving" | "unsaved";
type SaveError = {
  code?: string;
  field?: string;
  pair_id?: string;
  point_id?: string;
  portal_id?: string;
  server_draft_version?: number;
  server_updated_at?: string;
};
const RECOVERY_ONLY_ERROR_CODES = new Set([
  "assignment_not_editable",
  "assignment_task_unavailable",
  "batch_not_open",
]);

function recoveryHref(record: {
  assignment_id: string;
  base_version: number;
  draft_cycle_id: string;
  draft_state: AnnotationState;
  updated_at: string | null;
}): string {
  return `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(record))}`;
}

export function PocWireframe({
  hashState,
  metaContract,
  state,
  suspended = false,
}: {
  hashState?: (state: AnnotationState) => Promise<string>;
  metaContract?: MetaContract;
  state: AnnotationState;
  suspended?: boolean;
}) {
  const [result, setResult] = useState<{ state: AnnotationState; stateSha: string } | null>(null);
  const [status, setStatus] = useState<"calculating" | "current" | "error" | "stale">(
    "calculating",
  );

  useEffect(() => {
    let active = true;
    setStatus((current) => (current === "current" ? "stale" : "calculating"));
    if (suspended) {
      return () => {
        active = false;
      };
    }
    const timer = window.setTimeout(() => {
      void (hashState?.(state) ?? annotationStateSha(state, metaContract))
        .then((stateSha) => {
          if (!active) return;
          setResult({ state, stateSha });
          setStatus("current");
        })
        .catch(() => {
          if (active) setStatus("error");
        });
    }, 150);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [hashState, metaContract, state, suspended]);

  return (
    <section
      aria-label="本地 3D 预览（仅供参考）"
      data-authority="informational"
      data-engine-version="poc-wireframe-v1"
      data-preview-status={status}
      data-state-sha={result?.stateSha}
    >
      <h3>本地 3D 预览（仅供参考）</h3>
      <p>Local 3D preview (informational only)</p>
      <p>
        由当前 2D 标注生成，不参与本次 POC 的提交校验，也不会修改标注数据。 Generated from the
        current 2D annotation. It does not gate POC submission or modify annotation data.
      </p>
      {status === "error" ? <p role="alert">预览生成失败；仍可保存和提交。</p> : null}
      <svg aria-label="poc-wireframe-v1" viewBox="0 0 300 150">
        {result?.state.pairs.map((pair) => (
          <line
            key={pair.pair_id}
            stroke="currentColor"
            x1={pair.top.u * 300}
            x2={pair.bottom.u * 300}
            y1={pair.top.v * 150}
            y2={pair.bottom.v * 150}
          />
        ))}
      </svg>
      <output>poc-wireframe-v1 · state_sha {result?.stateSha ?? "calculating"}</output>
    </section>
  );
}

export function AssignmentWorkspace({
  activeTimeRuleVersion = ACTIVE_TIME_RULE_VERSION,
  assignmentId,
  locale = "zh-CN",
  metaContract,
  onSafeToSwitchChange,
  onSubmitted,
  online = true,
  tabId,
  writable = true,
}: {
  activeTimeRuleVersion?: string;
  assignmentId: string;
  locale?: SupportedLocale;
  metaContract?: MetaContract;
  onSafeToSwitchChange: (safe: boolean) => void;
  onSubmitted?: () => void;
  online?: boolean;
  tabId: string;
  writable?: boolean;
}) {
  const [draft, setDraft] = useState<DraftPayload | null>(null);
  const [editorGeneration, setEditorGeneration] = useState(0);
  const [loadError, setLoadError] = useState("");
  const [localState, setLocalState] = useState<AnnotationState | null>(null);
  const [reload, setReload] = useState(0);
  const [revision, setRevision] = useState<RevisionPayload | null>(null);
  const [recoverySavedAt, setRecoverySavedAt] = useState("");
  const [saveError, setSaveError] = useState<SaveError | null>(null);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("saved");
  const [submitError, setSubmitError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [transientEditing, setTransientEditing] = useState(false);
  const [unavailableRecovery, setUnavailableRecovery] = useState<DraftRecoveryRecord | null>(null);
  const [workspaceLost, setWorkspaceLost] = useState(false);
  const [activitySessionId] = useState(() => crypto.randomUUID());
  const activityTracker = useRef<ActivityTracker | null>(null);
  const currentState = useRef<AnnotationState | null>(null);
  const draftVersion = useRef(0);
  const idempotencyKey = useRef<string | null>(null);
  const initialHistory = useRef<AnnotationHistory | null>(null);
  const lastHistory = useRef<AnnotationHistory | null>(null);
  const mounted = useRef(true);
  const offlineDirty = useRef(false);
  const onlineRef = useRef(online);
  const queuedSave = useRef(false);
  const recoveryWrite = useRef<Promise<void>>(Promise.resolve());
  const saveBlocked = useRef(false);
  const saveInFlight = useRef(false);
  const saveTimer = useRef<number | undefined>(undefined);
  onlineRef.current = online;

  const persistHistory = useCallback(
    (history: AnnotationHistory) => {
      lastHistory.current = history;
      if (draft?.draft_cycle_id === undefined) return;
      const record: DraftRecoveryRecord = {
        assignment_id: assignmentId,
        base_version: draftVersion.current,
        draft_cycle_id: draft.draft_cycle_id,
        draft_state: history.present,
        key: assignmentId,
        pending_patch: offlineDirty.current ? { op: "replace", value: history.present } : null,
        undo_stack: { future: history.future, past: history.past },
        updated_at: new Date().toISOString(),
      };
      setRecoverySavedAt(record.updated_at);
      recoveryWrite.current = recoveryWrite.current
        .catch(() => undefined)
        .then(() => saveDraftRecovery(record))
        .catch(() => undefined);
    },
    [assignmentId, draft?.draft_cycle_id],
  );

  const postQueuedActivityEvent = useCallback(async (event: QueuedActivityEvent) => {
    if (!onlineRef.current) return;
    try {
      const response = await apiFetch("/api/worker/activity-events", {
        body: JSON.stringify(event),
        method: "POST",
      });
      if (response.ok) {
        await removeQueuedActivityEvent(event.event_id);
        return;
      }
      const body = (await response.json()) as { error?: { code?: string } };
      if (body.error?.code === "workspace_lease_lost" && mounted.current) {
        saveBlocked.current = true;
        setWorkspaceLost(true);
      }
    } catch {
      // The immutable event stays queued for the next online attempt.
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (saveTimer.current !== undefined) window.clearTimeout(saveTimer.current);
    };
  }, []);

  useEffect(() => {
    onSafeToSwitchChange(
      loadError !== "" ||
        revision !== null ||
        (draft !== null &&
          saveStatus === "saved" &&
          !submitting &&
          !transientEditing &&
          !workspaceLost &&
          submitError !== "submission_unavailable"),
    );
  }, [
    draft,
    loadError,
    onSafeToSwitchChange,
    revision,
    saveStatus,
    submitError,
    submitting,
    transientEditing,
    workspaceLost,
  ]);

  useEffect(() => {
    if (!writable) {
      saveBlocked.current = true;
      setWorkspaceLost(true);
    }
  }, [writable]);

  useEffect(() => {
    const controller = new AbortController();
    initialHistory.current = null;
    lastHistory.current = null;
    setDraft(null);
    setLoadError("");
    setRevision(null);
    setSaveError(null);
    setSubmitError("");
    setUnavailableRecovery(null);
    setWorkspaceLost(false);
    offlineDirty.current = false;
    saveBlocked.current = false;
    void Promise.all([
      apiFetch(
        `/api/worker/assignments/${assignmentId}/draft?tab_id=${encodeURIComponent(tabId)}`,
        {
          signal: controller.signal,
        },
      ),
      loadDraftRecovery(assignmentId).catch(() => null),
    ])
      .then(async ([response, recovery]) => {
        if (controller.signal.aborted) return;
        const pendingRecovery = recovery?.pending_patch === null ? null : recovery;
        setUnavailableRecovery(pendingRecovery);
        if (!response.ok) {
          const body = (await response.json()) as { error?: { code?: string } };
          throw new Error(body.error?.code ?? "draft_unavailable");
        }
        const payload = (await response.json()) as DraftPayload;
        if (controller.signal.aborted) return;
        setUnavailableRecovery(null);
        const recoveredHistory =
          pendingRecovery === null
            ? null
            : {
                future: pendingRecovery.undo_stack.future,
                past: pendingRecovery.undo_stack.past,
                present: pendingRecovery.draft_state,
              };
        const recoveredState = recoveredHistory?.present ?? payload.state;
        initialHistory.current = recoveredHistory;
        lastHistory.current = recoveredHistory;
        draftVersion.current = pendingRecovery?.base_version ?? payload.draft_version;
        currentState.current = recoveredState;
        setDraft(payload);
        setLocalState(recoveredState);
        setEditorGeneration((value) => value + 1);
        if (pendingRecovery === null) {
          setSaveStatus(onlineRef.current ? "saved" : "offline");
        } else if (
          pendingRecovery.base_version !== payload.draft_version ||
          pendingRecovery.draft_cycle_id !== payload.draft_cycle_id
        ) {
          saveBlocked.current = true;
          offlineDirty.current = true;
          setRecoverySavedAt(pendingRecovery.updated_at);
          setSaveError({
            code: "draft_conflict",
            server_draft_version: payload.draft_version,
            server_updated_at: payload.updated_at,
          });
          setSaveStatus("conflict");
        } else {
          offlineDirty.current = true;
          setRecoverySavedAt(pendingRecovery.updated_at);
          setSaveStatus(onlineRef.current ? "unsaved" : "offline");
          if (onlineRef.current) saveTimer.current = window.setTimeout(() => void saveNow(), 0);
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setLoadError(reason instanceof Error ? reason.message : "draft_unavailable");
        }
      });
    return () => controller.abort();
  }, [assignmentId, reload, tabId]);

  function discardRecoveryAndReload(): void {
    void clearDraftRecovery(assignmentId).finally(() => setReload((value) => value + 1));
  }

  useEffect(() => {
    if (draft?.draft_cycle_id === undefined) return;
    const tracker = new ActivityTracker(
      {
        active_time_rule_version: activeTimeRuleVersion,
        assignment_id: assignmentId,
        client_build_sha: CLIENT_BUILD_SHA,
        client_session_id: activitySessionId,
        draft_cycle_id: draft.draft_cycle_id,
      },
      (event) => {
        const queued = { ...event, tab_id: tabId };
        void queueActivityEvent(queued)
          .then(() => postQueuedActivityEvent(queued))
          .catch(() => undefined);
      },
      {
        focus: document.hasFocus(),
        visibility: document.visibilityState === "visible" ? "visible" : "hidden",
      },
    );
    activityTracker.current = tracker;
    const focus = () => tracker.setFocus(true);
    const blur = () => tracker.setFocus(false);
    const visibility = () =>
      tracker.setVisibility(document.visibilityState === "visible" ? "visible" : "hidden");
    window.addEventListener("focus", focus);
    window.addEventListener("blur", blur);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      tracker.setFocus(false);
      tracker.stop();
      if (activityTracker.current === tracker) activityTracker.current = null;
      window.removeEventListener("focus", focus);
      window.removeEventListener("blur", blur);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [
    activeTimeRuleVersion,
    activitySessionId,
    assignmentId,
    draft?.draft_cycle_id,
    postQueuedActivityEvent,
    tabId,
  ]);

  useEffect(() => {
    if (draft === null) return;
    if (!online) {
      if (saveTimer.current !== undefined) window.clearTimeout(saveTimer.current);
      setSaveStatus("offline");
      return;
    }
    void queuedActivityEvents(assignmentId).then((events) => {
      for (const event of events) void postQueuedActivityEvent(event);
    });
    if (offlineDirty.current && !saveBlocked.current) {
      setSaveStatus("unsaved");
      saveTimer.current = window.setTimeout(() => void saveNow(), 0);
    } else {
      setSaveStatus((current) => (current === "offline" ? "saved" : current));
    }
  }, [assignmentId, draft, online, postQueuedActivityEvent]);

  async function saveNow(): Promise<void> {
    if (saveBlocked.current || currentState.current === null) return;
    if (!onlineRef.current) {
      setSaveStatus("offline");
      return;
    }
    if (saveInFlight.current) {
      queuedSave.current = true;
      return;
    }
    const snapshot = currentState.current;
    saveInFlight.current = true;
    queuedSave.current = false;
    setSaveError(null);
    setSaveStatus("saving");
    try {
      const response = await apiFetch(`/api/worker/assignments/${assignmentId}/draft`, {
        body: JSON.stringify({
          expected_draft_version: draftVersion.current,
          state: snapshot,
          tab_id: tabId,
        }),
        method: "PUT",
      });
      if (!response.ok) {
        const body = (await response.json()) as { error?: SaveError };
        if (body.error?.code === "draft_conflict") {
          saveBlocked.current = true;
          setSaveError(body.error);
          setSaveStatus("conflict");
        } else if (body.error?.code === "workspace_lease_lost") {
          saveBlocked.current = true;
          setWorkspaceLost(true);
          setSaveStatus("error");
        } else if (
          body.error?.code !== undefined &&
          RECOVERY_ONLY_ERROR_CODES.has(body.error.code)
        ) {
          saveBlocked.current = true;
          setSaveError(body.error);
          setSaveStatus("error");
        } else {
          setSaveError(body.error ?? null);
          setSaveStatus("error");
        }
        return;
      }
      const payload = (await response.json()) as DraftPayload;
      if (!mounted.current) return;
      draftVersion.current = payload.draft_version;
      setDraft(payload);
      if (currentState.current === snapshot) {
        offlineDirty.current = false;
        setSaveStatus("saved");
      } else {
        offlineDirty.current = true;
        queuedSave.current = true;
        setSaveStatus("unsaved");
      }
      if (lastHistory.current !== null) persistHistory(lastHistory.current);
    } catch {
      if (mounted.current) {
        setSaveError(null);
        setSaveStatus(navigator.onLine ? "error" : "offline");
      }
    } finally {
      saveInFlight.current = false;
      if (queuedSave.current && !saveBlocked.current && mounted.current) {
        saveTimer.current = window.setTimeout(() => void saveNow(), 0);
      }
    }
  }

  function changed(next: AnnotationState): void {
    if (
      saveBlocked.current ||
      !writable ||
      submitting ||
      submitError === "submission_unavailable" ||
      workspaceLost
    )
      return;
    activityTracker.current?.recordInteraction("annotation_2d_edit");
    currentState.current = next;
    offlineDirty.current = true;
    idempotencyKey.current = null;
    setLocalState(next);
    setSaveStatus(onlineRef.current ? "unsaved" : "offline");
    if (!saveBlocked.current && onlineRef.current) {
      if (saveTimer.current !== undefined) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => void saveNow(), 300);
    }
  }

  async function submit(): Promise<void> {
    if (
      workspaceLost ||
      !writable ||
      !onlineRef.current ||
      draft === null ||
      localState === null ||
      saveStatus !== "saved" ||
      transientEditing ||
      !annotationStateSubmissionReady(localState)
    )
      return;
    const key = idempotencyKey.current ?? crypto.randomUUID();
    idempotencyKey.current = key;
    setSubmitting(true);
    setSubmitError("");
    try {
      const response = await apiFetch(`/api/worker/assignments/${assignmentId}/submit`, {
        body: JSON.stringify({
          client_build_sha: CLIENT_BUILD_SHA,
          expected_state_sha: draft.state_sha,
          idempotency_key: key,
          interaction_contract_version: INTERACTION_CONTRACT_VERSION,
          locale,
          tab_id: tabId,
          viewer_version: VIEWER_VERSION,
        }),
        method: "POST",
      });
      if (!response.ok) {
        const body = (await response.json()) as { error?: { code?: string } };
        if (body.error?.code === "draft_conflict") {
          saveBlocked.current = true;
          setSaveStatus("conflict");
        } else if (body.error?.code === "workspace_lease_lost") {
          saveBlocked.current = true;
          setWorkspaceLost(true);
          return;
        }
        setSubmitError(body.error?.code ?? "submission_failed");
        return;
      }
      const payload = (await response.json()) as RevisionPayload;
      saveBlocked.current = true;
      setRevision(payload);
      onSubmitted?.();
    } catch {
      setSubmitError("submission_unavailable");
    } finally {
      if (mounted.current) setSubmitting(false);
    }
  }

  if (loadError) {
    if (unavailableRecovery !== null) {
      return (
        <p role="alert">
          服务器草稿当前不可读取；本地恢复副本仍保留，且不会自动覆盖服务器数据。 / The server Draft
          is currently unavailable; the local recovery copy is preserved and will not overwrite
          server data automatically.{" "}
          <a
            download={`annotation-recovery-${assignmentId}.json`}
            href={recoveryHref({
              assignment_id: assignmentId,
              base_version: unavailableRecovery.base_version,
              draft_cycle_id: unavailableRecovery.draft_cycle_id,
              draft_state: unavailableRecovery.draft_state,
              updated_at: unavailableRecovery.updated_at,
            })}
          >
            导出本地恢复副本
          </a>{" "}
          <button onClick={() => setReload((value) => value + 1)} type="button">
            重试读取草稿
          </button>
        </p>
      );
    }
    if (loadError === "semi_prediction_unavailable") {
      return (
        <p role="alert">
          预测数据不可用，任务已技术阻断并等待管理员处理。 / Prediction data is unavailable; the
          task is technically blocked for administrator action.
        </p>
      );
    }
    return (
      <p role="alert">
        无法读取服务器草稿。
        <button onClick={() => setReload((value) => value + 1)} type="button">
          重试读取草稿
        </button>
      </p>
    );
  }
  if (draft === null || localState === null) return <p>正在读取服务器草稿…</p>;
  const recoveryDownload = (
    <a
      download={`annotation-recovery-${assignmentId}.json`}
      href={recoveryHref({
        assignment_id: assignmentId,
        base_version: draftVersion.current,
        draft_cycle_id: draft.draft_cycle_id,
        draft_state: localState,
        updated_at: recoverySavedAt || draft.updated_at || null,
      })}
    >
      导出本地恢复副本
    </a>
  );

  return (
    <AssignmentMedia assignmentId={assignmentId}>
      {(media, onVisibleMediaError) =>
        revision ? (
          <p role="status">
            Revision {revision.revision_no} 已提交：{revision.revision_id}
          </p>
        ) : (
          <div className="assignment-workspace">
            <AnnotationEditor
              backgroundImageUrl={media.url}
              disabled={
                saveStatus === "conflict" ||
                (saveError?.code !== undefined && RECOVERY_ONLY_ERROR_CODES.has(saveError.code)) ||
                submitting ||
                submitError === "submission_unavailable" ||
                workspaceLost ||
                !writable
              }
              initialState={localState}
              initialHistory={initialHistory.current ?? undefined}
              key={editorGeneration}
              locale={locale}
              metaContract={metaContract}
              onBackgroundImageError={onVisibleMediaError}
              onChange={changed}
              onHistoryChange={persistHistory}
              onTransientEditingChange={setTransientEditing}
            />
            {workspaceLost ? (
              <p role="alert">
                工作区已被接管；此页面已转为只读，不能继续编辑或提交。 本地恢复副本 base_version{" "}
                {draftVersion.current}
                {recoverySavedAt ? (
                  <>
                    ，保存于{" "}
                    <time dateTime={recoverySavedAt}>
                      {formatLocalTimestamp(recoverySavedAt, locale)}
                    </time>
                  </>
                ) : null}
                ；服务器版本不可读取。 {recoveryDownload}
              </p>
            ) : saveStatus === "conflict" ? (
              <p role="alert">
                草稿冲突：本地内容未覆盖服务器草稿。 本地恢复副本 base_version{" "}
                {draftVersion.current}
                {recoverySavedAt ? (
                  <>
                    ，保存于{" "}
                    <time dateTime={recoverySavedAt}>
                      {formatLocalTimestamp(recoverySavedAt, locale)}
                    </time>
                  </>
                ) : null}
                {saveError?.server_draft_version === undefined
                  ? ""
                  : `；服务器 draft_version ${saveError.server_draft_version}`}
                {saveError?.server_updated_at ? (
                  <>
                    ，服务器更新时间{" "}
                    <time dateTime={saveError.server_updated_at}>
                      {formatLocalTimestamp(saveError.server_updated_at, locale)}
                    </time>
                  </>
                ) : null}
                。
                <button onClick={discardRecoveryAndReload} type="button">
                  重新加载服务器草稿
                </button>
                {recoveryDownload}
              </p>
            ) : saveError?.code !== undefined && RECOVERY_ONLY_ERROR_CODES.has(saveError.code) ? (
              <p role="alert">
                服务器状态已变化（{saveError.code}）；本地内容未自动合并或覆盖。 本地恢复副本
                base_version {draftVersion.current}
                {recoverySavedAt ? (
                  <>
                    ，保存于{" "}
                    <time dateTime={recoverySavedAt}>
                      {formatLocalTimestamp(recoverySavedAt, locale)}
                    </time>
                  </>
                ) : null}
                。{recoveryDownload}
              </p>
            ) : saveStatus === "error" ? (
              <p role="alert">
                {saveError ? (
                  <>
                    保存失败：{saveError.code ?? "draft_save_failed"}
                    {saveError.field ? `；字段 ${saveError.field}` : ""}
                    {saveError.portal_id ? `；Portal ${saveError.portal_id}` : ""}。
                    {saveError.portal_id ? (
                      <a href={`#portal-${saveError.portal_id}`}>定位 Portal</a>
                    ) : null}
                  </>
                ) : (
                  "保存失败。"
                )}
                <button onClick={() => void saveNow()} type="button">
                  重试保存
                </button>
              </p>
            ) : (
              <p aria-live="polite" role="status">
                {saveStatus === "saving"
                  ? "保存中"
                  : saveStatus === "saved"
                    ? "已保存"
                    : saveStatus === "offline"
                      ? "离线（本地保存）"
                      : "未保存"}
              </p>
            )}
            <PocWireframe
              metaContract={metaContract}
              state={localState}
              suspended={transientEditing}
            />
            <p>{locale === "zh-CN" ? "提交语言：简体中文" : "Submission language: English"}</p>
            <button
              disabled={
                saveStatus !== "saved" ||
                !online ||
                submitting ||
                transientEditing ||
                workspaceLost ||
                !writable ||
                !annotationStateSubmissionReady(localState)
              }
              onClick={() => void submit()}
              type="button"
            >
              {submitting ? "提交中…" : "提交 Revision"}
            </button>
            {submitError ? (
              <p role="alert">
                {submitError === "submission_unavailable"
                  ? "提交结果未知，请重试；系统会使用同一幂等键且不会重复创建 Revision。"
                  : `提交失败：${submitError}`}
              </p>
            ) : null}
          </div>
        )
      }
    </AssignmentMedia>
  );
}
