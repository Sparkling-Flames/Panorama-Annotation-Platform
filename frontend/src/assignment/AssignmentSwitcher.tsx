import { type FormEvent, useEffect, useState } from "react";

import { apiFetch } from "../api";
import type { MetaContract } from "../annotationState";
import { formatLocalTimestamp, type SupportedLocale } from "../locale";
import { AssignmentWorkspace } from "./AssignmentWorkspace";
import { GuidanceInbox } from "./GuidanceInbox";

type WorkBatch = {
  batch_id: string;
  name: string;
  status: "closed" | "frozen" | "open";
};

type Assignment = {
  assignment_id: string;
  batch_id: string;
  order_index: number;
  queue_state: "deferred" | "needs_revisit" | "ready";
  review_state: "accepted" | "adjudicated" | "changes_requested" | "closed" | "unreviewed";
  task: {
    active_time_rule_version: string;
    external_task_key: string;
    meta_contract?: MetaContract;
    mode: "manual" | "semi";
    task_id: string;
  };
  work_state: "assigned" | "blocked" | "in_progress" | "revoked" | "submitted";
};

type BlockReason =
  | "conflict_of_interest"
  | "image_unavailable"
  | "other"
  | "technical_failure"
  | "temporary_worker_issue"
  | "unable_to_complete";

type ReworkRequest = {
  assignment_id: string;
  due_at: string;
  instruction: string;
  request_id: string;
  status: "completed" | "in_progress" | "pending" | "rework_overdue";
};

const reworkStatusLabel: Record<ReworkRequest["status"], string> = {
  completed: "已完成 / Completed",
  in_progress: "返工中 / In progress",
  pending: "待处理 / Pending",
  rework_overdue: "已逾期 / Overdue",
};

function assignmentLabel(assignment: Assignment): string {
  return assignment.task.external_task_key || assignment.task.task_id;
}

function nextOrderedAssignment(assignments: Assignment[], current: Assignment): Assignment | null {
  const unfinished = assignments
    .filter((assignment) => ["assigned", "in_progress"].includes(assignment.work_state))
    .sort((left, right) => left.order_index - right.order_index);
  return (
    unfinished.find(
      (assignment) =>
        assignment.order_index > current.order_index && assignment.queue_state !== "deferred",
    ) ??
    unfinished.find((assignment) => assignment.queue_state === "deferred") ??
    null
  );
}

export function AssignmentSwitcher({
  online = true,
  tabId,
  writable = true,
}: {
  online?: boolean;
  tabId: string;
  writable?: boolean;
}) {
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [locale, setLocale] = useState<SupportedLocale>("zh-CN");
  const [batches, setBatches] = useState<WorkBatch[]>([]);
  const [blockDraft, setBlockDraft] = useState<{
    assignmentId: string;
    reasonCode: BlockReason;
    reasonText: string;
  } | null>(null);
  const [busyAssignmentId, setBusyAssignmentId] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [listRetry, setListRetry] = useState(0);
  const [loadingAssignments, setLoadingAssignments] = useState(false);
  const [loadingBatches, setLoadingBatches] = useState(true);
  const [reworkRequests, setReworkRequests] = useState<ReworkRequest[]>([]);
  const [selectedAssignmentId, setSelectedAssignmentId] = useState<string | null>(null);
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [workspaceSafe, setWorkspaceSafe] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    setLoadingBatches(true);
    void apiFetch("/api/worker/batches", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("batch list unavailable");
        }
        const payload = (await response.json()) as { batches: WorkBatch[] };
        setBatches(payload.batches);
        setSelectedBatchId((current) => current ?? payload.batches[0]?.batch_id ?? null);
        setLoadingBatches(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError(true);
          setLoadingBatches(false);
        }
      });
    return () => controller.abort();
  }, [listRetry]);

  useEffect(() => {
    if (selectedBatchId === null) {
      setAssignments([]);
      return;
    }
    const controller = new AbortController();
    setAssignments([]);
    setSelectedAssignmentId(null);
    setWorkspaceSafe(true);
    setError(false);
    setLoadingAssignments(true);
    void apiFetch(`/api/worker/batches/${selectedBatchId}/assignments`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("assignment list unavailable");
        }
        const payload = (await response.json()) as { assignments: Assignment[] };
        setAssignments(payload.assignments);
        setLoadingAssignments(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError(true);
          setLoadingAssignments(false);
        }
      });
    return () => controller.abort();
  }, [listRetry, selectedBatchId]);

  const hasReviewFeedback = assignments.some(
    (assignment) => assignment.review_state === "changes_requested",
  );

  useEffect(() => {
    if (!hasReviewFeedback) {
      setReworkRequests([]);
      return;
    }
    const controller = new AbortController();
    void apiFetch("/api/worker/rework-requests", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("rework list unavailable");
        const payload = (await response.json()) as { requests: ReworkRequest[] };
        setReworkRequests(payload.requests);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, [hasReviewFeedback, listRetry]);

  async function updateAssignment(
    assignment: Assignment,
    action: "open" | "queue-state" | "revise",
    queueState?: Assignment["queue_state"],
  ): Promise<Assignment | null> {
    if (!online || !writable) return null;
    const opensWorkspace = action === "open" || action === "revise";
    setBusyAssignmentId(assignment.assignment_id);
    setError(false);
    if (opensWorkspace) {
      setSelectedAssignmentId(null);
      setWorkspaceSafe(false);
    }
    try {
      const response = await apiFetch(
        `/api/worker/assignments/${assignment.assignment_id}/${action}`,
        {
          body: JSON.stringify({
            ...(queueState === undefined ? {} : { queue_state: queueState }),
            tab_id: tabId,
          }),
          method: "POST",
        },
      );
      if (!response.ok) {
        throw new Error("assignment update rejected");
      }
      const updated = (await response.json()) as Assignment;
      setAssignments((current) =>
        current.map((item) => (item.assignment_id === updated.assignment_id ? updated : item)),
      );
      if (opensWorkspace) {
        setWorkspaceSafe(false);
        setSelectedAssignmentId(updated.assignment_id);
      }
      return updated;
    } catch {
      setError(true);
      if (opensWorkspace) setWorkspaceSafe(true);
      return null;
    } finally {
      setBusyAssignmentId(null);
    }
  }

  async function deferAndContinue(assignment: Assignment): Promise<void> {
    const updated = await updateAssignment(assignment, "queue-state", "deferred");
    if (updated === null) return;
    const updatedAssignments = assignments.map((item) =>
      item.assignment_id === updated.assignment_id ? updated : item,
    );
    const next = nextOrderedAssignment(updatedAssignments, updated);
    if (next !== null) await updateAssignment(next, "open");
  }

  async function acceptRework(rework: ReworkRequest): Promise<void> {
    const assignment = assignments.find((item) => item.assignment_id === rework.assignment_id);
    if (assignment === undefined || !online || !writable || !workspaceSafe) return;
    setBusyAssignmentId(assignment.assignment_id);
    setError(false);
    try {
      const response = await apiFetch(`/api/worker/rework-requests/${rework.request_id}/accept`, {
        body: JSON.stringify({ tab_id: tabId }),
        method: "POST",
      });
      if (!response.ok) throw new Error("rework request rejected");
      const updatedRework = (await response.json()) as ReworkRequest;
      setReworkRequests((current) =>
        current.map((item) =>
          item.request_id === updatedRework.request_id ? updatedRework : item,
        ),
      );
      setAssignments((current) =>
        current.map((item) =>
          item.assignment_id === assignment.assignment_id
            ? { ...item, work_state: "in_progress" }
            : item,
        ),
      );
      setWorkspaceSafe(false);
      setSelectedAssignmentId(assignment.assignment_id);
    } catch {
      setError(true);
    } finally {
      setBusyAssignmentId(null);
    }
  }

  async function reportBlock(
    event: FormEvent<HTMLFormElement>,
    assignment: Assignment,
  ): Promise<void> {
    event.preventDefault();
    if (
      !online ||
      !writable ||
      blockDraft === null ||
      blockDraft.assignmentId !== assignment.assignment_id
    )
      return;
    setBusyAssignmentId(assignment.assignment_id);
    setError(false);
    try {
      const response = await apiFetch(`/api/worker/assignments/${assignment.assignment_id}/block`, {
        body: JSON.stringify({
          reason_code: blockDraft.reasonCode,
          reason_text: blockDraft.reasonText,
          tab_id: tabId,
        }),
        method: "POST",
      });
      if (!response.ok) throw new Error("block report rejected");
      const updated = (await response.json()) as Assignment;
      const updatedAssignments = assignments.map((item) =>
        item.assignment_id === updated.assignment_id ? updated : item,
      );
      setAssignments(updatedAssignments);
      setBlockDraft(null);
      const next = nextOrderedAssignment(updatedAssignments, updated);
      if (next === null) {
        setSelectedAssignmentId(null);
        setWorkspaceSafe(true);
      } else {
        await updateAssignment(next, "open");
      }
    } catch {
      setError(true);
    } finally {
      setBusyAssignmentId(null);
    }
  }

  function submittedAndContinue(assignmentId: string): void {
    const updatedAssignments = assignments.map((assignment) =>
      assignment.assignment_id === assignmentId
        ? { ...assignment, work_state: "submitted" as const }
        : assignment,
    );
    setAssignments(updatedAssignments);
    setReworkRequests((current) =>
      current.map((item) =>
        item.assignment_id === assignmentId ? { ...item, status: "completed" } : item,
      ),
    );
    const submitted = updatedAssignments.find(
      (assignment) => assignment.assignment_id === assignmentId,
    );
    const next =
      submitted === undefined ? null : nextOrderedAssignment(updatedAssignments, submitted);
    if (next === null) {
      setSelectedAssignmentId(null);
      setWorkspaceSafe(true);
      return;
    }
    void updateAssignment(next, "open");
  }

  const selectedAssignment = assignments.find(
    (assignment) => assignment.assignment_id === selectedAssignmentId,
  );

  return (
    <section aria-label="我的批次任务">
      <h2>我的批次任务</h2>
      <label>
        {locale === "zh-CN" ? "界面语言" : "Interface language"}
        <select
          aria-label={locale === "zh-CN" ? "界面语言" : "Interface language"}
          disabled={!workspaceSafe || busyAssignmentId !== null}
          onChange={(event) => setLocale(event.currentTarget.value as SupportedLocale)}
          value={locale}
        >
          <option value="zh-CN">简体中文</option>
          <option value="en">English</option>
        </select>
      </label>
      {loadingBatches ? <p role="status">正在读取批次…</p> : null}
      {!loadingBatches && batches.length === 0 && !error ? <p>当前没有已分配批次。</p> : null}
      {batches.length > 1 ? (
        <nav aria-label="批次选择">
          {batches.map((batch) => (
            <button
              aria-pressed={batch.batch_id === selectedBatchId}
              disabled={
                busyAssignmentId !== null ||
                blockDraft !== null ||
                !workspaceSafe ||
                !online ||
                !writable
              }
              key={batch.batch_id}
              onClick={() => {
                if (!online || !writable) return;
                setAssignments([]);
                setSelectedAssignmentId(null);
                setWorkspaceSafe(true);
                setSelectedBatchId(batch.batch_id);
              }}
              type="button"
            >
              {batch.name}
            </button>
          ))}
        </nav>
      ) : null}
      {loadingAssignments ? <p role="status">正在读取 Assignment…</p> : null}
      {error ? (
        <p role="alert">
          无法读取或更新 Assignment。
          <button onClick={() => setListRetry((value) => value + 1)} type="button">
            重试读取
          </button>
        </p>
      ) : null}
      {reworkRequests.length > 0 ? (
        <section aria-label="返工通知 / Rework notifications">
          <h3>返工通知 / Rework notifications</h3>
          <ul>
            {reworkRequests.map((rework) => (
              <li key={rework.request_id}>
                <p>{rework.instruction}</p>
                <p>状态 / Status: {reworkStatusLabel[rework.status]}</p>
                <p>
                  截止时间 / Due:{" "}
                  <time dateTime={rework.due_at}>
                    {formatLocalTimestamp(rework.due_at, locale)}
                  </time>
                </p>
                {rework.status === "pending" || rework.status === "rework_overdue" ? (
                  <button
                    disabled={busyAssignmentId !== null || !online || !writable || !workspaceSafe}
                    onClick={() => void acceptRework(rework)}
                    type="button"
                  >
                    开始返工 / Start rework
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <GuidanceInbox />
      <ul>
        {assignments.map((assignment) => {
          const label = assignmentLabel(assignment);
          const busy = busyAssignmentId !== null || !online || !writable;
          return (
            <li key={assignment.assignment_id}>
              <h3>{label}</h3>
              <p>队列：{assignment.queue_state}</p>
              <p>工作：{assignment.work_state}</p>
              {assignment.work_state === "submitted" ? (
                <button
                  disabled={
                    busy ||
                    blockDraft !== null ||
                    !workspaceSafe ||
                    assignment.review_state === "changes_requested"
                  }
                  onClick={() => void updateAssignment(assignment, "revise")}
                  type="button"
                >
                  修订 {label}
                </button>
              ) : (
                <button
                  disabled={
                    busy ||
                    blockDraft !== null ||
                    !workspaceSafe ||
                    !["assigned", "in_progress"].includes(assignment.work_state)
                  }
                  onClick={() => void updateAssignment(assignment, "open")}
                  type="button"
                >
                  打开 {label}
                </button>
              )}
              {!["blocked", "submitted", "revoked"].includes(assignment.work_state) ? (
                <>
                  <button
                    disabled={
                      busy ||
                      blockDraft !== null ||
                      !workspaceSafe ||
                      assignment.queue_state === "deferred"
                    }
                    onClick={() => void deferAndContinue(assignment)}
                    type="button"
                  >
                    暂缓 {label}
                  </button>
                  <button
                    disabled={busy || blockDraft !== null || !workspaceSafe}
                    onClick={() =>
                      setBlockDraft({
                        assignmentId: assignment.assignment_id,
                        reasonCode: "unable_to_complete",
                        reasonText: "",
                      })
                    }
                    type="button"
                  >
                    报告阻断 {label}
                  </button>
                </>
              ) : null}
              {blockDraft?.assignmentId === assignment.assignment_id ? (
                <form onSubmit={(event) => void reportBlock(event, assignment)}>
                  <label>
                    阻断原因
                    <select
                      onChange={(event) =>
                        setBlockDraft({
                          ...blockDraft,
                          reasonCode: event.target.value as BlockReason,
                        })
                      }
                      value={blockDraft.reasonCode}
                    >
                      <option value="technical_failure">技术故障</option>
                      <option value="image_unavailable">图片不可用</option>
                      <option value="temporary_worker_issue">工人临时问题</option>
                      <option value="conflict_of_interest">利益冲突</option>
                      <option value="unable_to_complete">无法完成</option>
                      <option value="other">其他</option>
                    </select>
                  </label>
                  <label>
                    说明
                    <textarea
                      onChange={(event) =>
                        setBlockDraft({ ...blockDraft, reasonText: event.target.value })
                      }
                      required={["technical_failure", "other"].includes(blockDraft.reasonCode)}
                      value={blockDraft.reasonText}
                    />
                  </label>
                  <button disabled={busy} type="submit">
                    确认报告阻断
                  </button>
                  <button disabled={busy} onClick={() => setBlockDraft(null)} type="button">
                    取消
                  </button>
                </form>
              ) : null}
            </li>
          );
        })}
      </ul>
      {selectedAssignment ? (
        <>
          <p>当前 Assignment：{assignmentLabel(selectedAssignment)}</p>
          <AssignmentWorkspace
            activeTimeRuleVersion={selectedAssignment.task.active_time_rule_version}
            assignmentId={selectedAssignment.assignment_id}
            key={selectedAssignment.assignment_id}
            locale={locale}
            metaContract={selectedAssignment.task.meta_contract}
            online={online}
            onSafeToSwitchChange={setWorkspaceSafe}
            onSubmitted={() => submittedAndContinue(selectedAssignment.assignment_id)}
            tabId={tabId}
            writable={writable}
          />
        </>
      ) : null}
    </section>
  );
}
