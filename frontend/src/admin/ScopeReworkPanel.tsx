import { type FormEvent, useState } from "react";

import { apiFetch } from "../api";

const DEFAULT_INSTRUCTION =
  "最终范围已裁定为可标注。请只依据你自己的观察返工，不要复制其他工人的几何或 Portal。 / Final scope is annotatable. Rework only from your own observations. Do not copy another worker's geometry or portals.";

type RevisionSummary = {
  state: {
    geometry_attempt_status: string | null;
    pairs: unknown[];
    portals: unknown[];
    scope_reason_codes: string[];
    worker_scope_observation: string | null;
  };
  state_sha: string;
  worker_id: string;
};

function isEligible(summary: RevisionSummary): boolean {
  return ["needs_scope_review", "representation_oos"].includes(
    summary.state.worker_scope_observation ?? "",
  );
}

export function ScopeReworkPanel({ revisionId }: { revisionId: string }) {
  const [summary, setSummary] = useState<RevisionSummary | null>(null);
  const [reason, setReason] = useState("");
  const [instruction, setInstruction] = useState(DEFAULT_INSTRUCTION);
  const [dueAt, setDueAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(false);
  const [error, setError] = useState("");

  async function loadSummary(): Promise<void> {
    if (busy || summary !== null) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(`/api/admin/revisions/${revisionId}`);
      if (!response.ok) throw new Error("revision unavailable");
      setSummary((await response.json()) as RevisionSummary);
    } catch {
      setError("无法读取 Revision / Revision unavailable");
    } finally {
      setBusy(false);
    }
  }

  async function createRework(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (busy || summary === null || !isEligible(summary)) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(`/api/admin/revisions/${revisionId}/scope-rework`, {
        body: JSON.stringify({
          due_at: new Date(dueAt).toISOString(),
          instruction,
          reason,
        }),
        method: "POST",
      });
      if (!response.ok) throw new Error("scope rework rejected");
      setCreated(true);
    } catch {
      setError("无法创建 Scope 返工 / Scope rework unavailable");
    } finally {
      setBusy(false);
    }
  }

  if (summary === null) {
    return (
      <div className="scope-rework-panel">
        <button disabled={busy} onClick={() => void loadSummary()} type="button">
          {busy ? "读取中… / Loading…" : "复核 Scope / Review scope"}
        </button>
        {error ? <p role="alert">{error}</p> : null}
      </div>
    );
  }

  return (
    <section aria-label={`Scope rework ${revisionId}`} className="scope-rework-panel">
      <dl>
        <div>
          <dt>工人 / Worker</dt>
          <dd>{summary.worker_id}</dd>
        </div>
        <div>
          <dt>范围观察 / Scope observation</dt>
          <dd>{summary.state.worker_scope_observation ?? "—"}</dd>
        </div>
        <div>
          <dt>几何完成度 / Geometry attempt</dt>
          <dd>{summary.state.geometry_attempt_status ?? "—"}</dd>
        </div>
        <div>
          <dt>证据摘要 / Evidence summary</dt>
          <dd>
            {summary.state.pairs.length} 对 / pairs · {summary.state.portals.length} 个 / portals
          </dd>
        </div>
        <div>
          <dt>范围原因 / Scope reasons</dt>
          <dd>{summary.state.scope_reason_codes.join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>状态 SHA / State SHA</dt>
          <dd>{summary.state_sha}</dd>
        </div>
      </dl>
      {isEligible(summary) ? (
        <form onSubmit={(event) => void createRework(event)}>
          <p>
            这是 Scope-only 裁定：源 Revision 的 geometry/portal 将原样保留，但不会选择或替换 Task
            的正式交付结果。指导中不得粘贴其他工人的 geometry 或 portal。 / This scope-only action
            preserves source geometry and portals but does not select or replace the Task delivery.
            Do not paste another worker&apos;s geometry or portals into the instruction.
          </p>
          <p>最终处置 / Final disposition: annotatable</p>
          <label>
            裁定原因 / Decision reason
            <textarea
              maxLength={2000}
              onChange={(event) => setReason(event.target.value)}
              required
              value={reason}
            />
          </label>
          <label>
            工人指导 / Worker instruction
            <textarea
              maxLength={2000}
              onChange={(event) => setInstruction(event.target.value)}
              required
              value={instruction}
            />
          </label>
          <label>
            截止时间 / Due time
            <input
              onChange={(event) => setDueAt(event.target.value)}
              required
              type="datetime-local"
              value={dueAt}
            />
          </label>
          <button disabled={busy || created} type="submit">
            {busy ? "创建中… / Creating…" : "创建 Scope 返工 / Create rework"}
          </button>
          {created ? <p role="status">Scope 返工已创建 / Scope rework created</p> : null}
          {error ? <p role="alert">{error}</p> : null}
        </form>
      ) : (
        <p>该 Revision 不符合 Scope 返工条件 / Revision is not eligible for scope rework</p>
      )}
    </section>
  );
}
