import { useEffect, useState } from "react";

import { apiFetch } from "../api";

type WorkBatch = {
  batch_id: string;
  name: string;
  status: "closed" | "frozen" | "open";
};

type Assignment = {
  assignment_id: string;
  batch_id: string;
  queue_state: "deferred" | "needs_revisit" | "ready";
  review_state: "accepted" | "adjudicated" | "changes_requested" | "closed" | "unreviewed";
  task: {
    external_task_key: string;
    mode: "manual" | "semi";
    task_id: string;
  };
  work_state: "assigned" | "in_progress" | "revoked" | "skipped" | "submitted";
};

function assignmentLabel(assignment: Assignment): string {
  return assignment.task.external_task_key || assignment.task.task_id;
}

export function AssignmentSwitcher({ tabId }: { tabId: string }) {
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [batches, setBatches] = useState<WorkBatch[]>([]);
  const [busyAssignmentId, setBusyAssignmentId] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [selectedAssignmentId, setSelectedAssignmentId] = useState<string | null>(null);
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void apiFetch("/api/worker/batches", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("batch list unavailable");
        }
        const payload = (await response.json()) as { batches: WorkBatch[] };
        setBatches(payload.batches);
        setSelectedBatchId((current) => current ?? payload.batches[0]?.batch_id ?? null);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError(true);
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (selectedBatchId === null) {
      setAssignments([]);
      return;
    }
    const controller = new AbortController();
    setError(false);
    void apiFetch(`/api/worker/batches/${selectedBatchId}/assignments`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("assignment list unavailable");
        }
        const payload = (await response.json()) as { assignments: Assignment[] };
        setAssignments(payload.assignments);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError(true);
        }
      });
    return () => controller.abort();
  }, [selectedBatchId]);

  async function updateAssignment(
    assignment: Assignment,
    action: "open" | "queue-state",
    queueState?: Assignment["queue_state"],
  ): Promise<void> {
    setBusyAssignmentId(assignment.assignment_id);
    setError(false);
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
      if (action === "open") {
        setSelectedAssignmentId(updated.assignment_id);
      }
    } catch {
      setError(true);
    } finally {
      setBusyAssignmentId(null);
    }
  }

  const selectedAssignment = assignments.find(
    (assignment) => assignment.assignment_id === selectedAssignmentId,
  );

  return (
    <section aria-label="我的批次任务">
      <h2>我的批次任务</h2>
      {batches.length === 0 && !error ? <p>当前没有已分配批次。</p> : null}
      {batches.length > 1 ? (
        <nav aria-label="批次选择">
          {batches.map((batch) => (
            <button
              aria-pressed={batch.batch_id === selectedBatchId}
              key={batch.batch_id}
              onClick={() => setSelectedBatchId(batch.batch_id)}
              type="button"
            >
              {batch.name}
            </button>
          ))}
        </nav>
      ) : null}
      {error ? <p role="alert">无法读取或更新 Assignment。</p> : null}
      <ul>
        {assignments.map((assignment) => {
          const label = assignmentLabel(assignment);
          const busy = busyAssignmentId === assignment.assignment_id;
          return (
            <li key={assignment.assignment_id}>
              <h3>{label}</h3>
              <p>队列：{assignment.queue_state}</p>
              <p>工作：{assignment.work_state}</p>
              <button
                disabled={busy || assignment.work_state === "revoked"}
                onClick={() => void updateAssignment(assignment, "open")}
                type="button"
              >
                打开 {label}
              </button>
              <button
                disabled={busy || assignment.queue_state === "deferred"}
                onClick={() => void updateAssignment(assignment, "queue-state", "deferred")}
                type="button"
              >
                暂缓 {label}
              </button>
              <button
                disabled={busy || assignment.queue_state === "needs_revisit"}
                onClick={() => void updateAssignment(assignment, "queue-state", "needs_revisit")}
                type="button"
              >
                稍后复访 {label}
              </button>
              <button
                disabled={busy || assignment.queue_state === "ready"}
                onClick={() => void updateAssignment(assignment, "queue-state", "ready")}
                type="button"
              >
                设为就绪 {label}
              </button>
            </li>
          );
        })}
      </ul>
      {selectedAssignment ? <p>当前 Assignment：{assignmentLabel(selectedAssignment)}</p> : null}
    </section>
  );
}
