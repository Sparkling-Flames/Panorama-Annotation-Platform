import { useEffect, useState } from "react";

import { apiFetch } from "../api";
import { formatLocalTimestamp } from "../locale";

type WorkBatch = { batch_id: string; name: string; status: string };

type Counts = {
  active_workers: number;
  block_reports: number;
  deferred: number;
  event_errors: number;
  media_errors: number;
  pending: number;
  resolved: number;
  revisions: number;
  save_errors: number;
  structure_blocks: number;
  submitted: number;
  unresolved: number;
  verification_pending: number;
};

type Snapshot = {
  batch_id: string;
  counts: Counts;
  max_event_delay_ms: number;
  metric_snapshot?: MetricSnapshot | null;
  refreshed_at: string;
  workers: WorkerStatistics[];
};

type WorkerStatistics = {
  accepted: number;
  active_time_ms_by_rule: Record<string, number>;
  block_reports: number;
  deferred: number;
  rework_requests: number;
  scope_observations: Record<string, number>;
  submitted: number;
  worker_id: string;
};

type MetricSnapshot = {
  input_count: number;
  missing: number;
  not_evaluable: number;
  provisional: true;
  result: Record<string, number>;
  reused_from_snapshot_id?: string | null;
  snapshot_id: string;
  status: "failed" | "pending" | "running" | "succeeded";
  support: number;
};

type ReviewQueueItem = {
  conflict_summary: {
    reason_codes: string[];
  };
  input_revision_ids: string[];
  input_sha256: string | null;
  queue_id?: string;
  queue_type: "audit_finding" | "consensus_unresolved" | "operational_issue";
  rule_versions: Record<string, string>;
  task_id: string;
  updated_at: string;
};

const COUNT_LABELS: ReadonlyArray<[keyof Counts, string]> = [
  ["submitted", "已提交任务 / Submitted tasks"],
  ["pending", "待处理任务 / Pending tasks"],
  ["resolved", "已解决任务 / Resolved tasks"],
  ["unresolved", "未解决任务 / Unresolved tasks"],
  ["active_workers", "当前活跃工人 / Active workers"],
  ["revisions", "Revision 数量 / Revisions"],
  ["verification_pending", "待验证 / Verification pending"],
  ["deferred", "暂时跳过 / Deferred"],
  ["block_reports", "阻断报告 / Block reports"],
  ["structure_blocks", "结构阻断 / Structure blocks"],
  ["save_errors", "保存错误 / Save errors"],
  ["media_errors", "媒体错误 / Media errors"],
  ["event_errors", "事件错误 / Event errors"],
];

export function AdminOperations() {
  const [batches, setBatches] = useState<WorkBatch[] | null>(null);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [reviewQueue, setReviewQueue] = useState<ReviewQueueItem[] | null>(null);
  const [metricSnapshot, setMetricSnapshot] = useState<MetricSnapshot | null>(null);
  const [calculating, setCalculating] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void apiFetch("/api/admin/work-batches", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("batches unavailable");
        const payload = (await response.json()) as { batches?: WorkBatch[] };
        if (!Array.isArray(payload.batches)) throw new Error("invalid batches");
        setBatches(payload.batches);
        setSelectedBatchId((current) => current || payload.batches?.[0]?.batch_id || "");
      })
      .catch((reason: unknown) => {
        if ((reason as { name?: string }).name !== "AbortError")
          setError("无法读取批次 / Batches unavailable");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedBatchId) return;
    const controller = new AbortController();
    setError("");
    void apiFetch(`/api/admin/work-batches/${selectedBatchId}/operations`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("operations unavailable");
        const payload = (await response.json()) as Snapshot;
        setSnapshot(payload);
        setMetricSnapshot(payload.metric_snapshot ?? null);
      })
      .catch((reason: unknown) => {
        if ((reason as { name?: string }).name !== "AbortError")
          setError("无法读取运营数据 / Operations unavailable");
      });
    return () => controller.abort();
  }, [refresh, selectedBatchId]);

  useEffect(() => {
    if (!selectedBatchId) return;
    const controller = new AbortController();
    void apiFetch(`/api/admin/work-batches/${selectedBatchId}/review-queue`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("review queue unavailable");
        const payload = (await response.json()) as { items?: ReviewQueueItem[] };
        if (!Array.isArray(payload.items)) throw new Error("invalid review queue");
        setReviewQueue(payload.items);
      })
      .catch((reason: unknown) => {
        if ((reason as { name?: string }).name !== "AbortError")
          setError("无法读取复核队列 / Review queue unavailable");
      });
    return () => controller.abort();
  }, [refresh, selectedBatchId]);

  async function calculateSnapshot() {
    if (!selectedBatchId || calculating) return;
    setCalculating(true);
    setError("");
    try {
      const response = await apiFetch(
        `/api/admin/work-batches/${selectedBatchId}/metric-snapshots`,
        { body: "{}", method: "POST" },
      );
      if (!response.ok) throw new Error("snapshot unavailable");
      setMetricSnapshot((await response.json()) as MetricSnapshot);
    } catch {
      setError("无法创建临时快照 / Provisional snapshot unavailable");
    } finally {
      setCalculating(false);
    }
  }

  return (
    <section aria-labelledby="admin-operations-heading" className="admin-operations">
      <h2 id="admin-operations-heading">批次运营概览 / Batch operations</h2>
      {batches === null && !error ? <p role="status">正在读取批次… / Loading batches…</p> : null}
      {batches?.length === 0 ? <p>暂无批次 / No batches yet</p> : null}
      {batches && batches.length > 0 ? (
        <div className="admin-operations-controls">
          <label>
            批次 / Batch
            <select
              onChange={(event) => {
                setSelectedBatchId(event.target.value);
                setSnapshot(null);
                setReviewQueue(null);
                setMetricSnapshot(null);
              }}
              value={selectedBatchId}
            >
              {batches.map((batch) => (
                <option key={batch.batch_id} value={batch.batch_id}>
                  {batch.name} ({batch.status})
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => setRefresh((value) => value + 1)} type="button">
            刷新 / Refresh
          </button>
          <button disabled={calculating} onClick={() => void calculateSnapshot()} type="button">
            {calculating ? "计算中… / Calculating…" : "计算当前情况 / Calculate current snapshot"}
          </button>
        </div>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
      {snapshot ? (
        <>
          <p>
            刷新时间 / Refreshed:{" "}
            <time dateTime={snapshot.refreshed_at}>
              {formatLocalTimestamp(snapshot.refreshed_at, "zh-CN")}
            </time>
          </p>
          <dl className="admin-operations-grid">
            {COUNT_LABELS.map(([key, label]) => (
              <div key={key}>
                <dt>{label}</dt>
                <dd>{snapshot.counts[key]}</dd>
              </div>
            ))}
            <div>
              <dt>最大事件延迟 / Max event delay</dt>
              <dd>{snapshot.max_event_delay_ms} ms</dd>
            </div>
          </dl>
          <h3>工人可核验统计 / Verifiable worker statistics</h3>
          <p>不计算工资或付款 / No wage or payment calculation</p>
          <table>
            <thead>
              <tr>
                <th>工人 ID / Worker ID</th>
                <th>提交 / Submitted</th>
                <th>接受 / Accepted</th>
                <th>暂时跳过 / Deferred</th>
                <th>阻断 / Blocks</th>
                <th>返工 / Reworks</th>
                <th>Scope observations</th>
                <th>按规则版本计时 / Time by rule</th>
              </tr>
            </thead>
            <tbody>
              {snapshot.workers.map((worker) => (
                <tr key={worker.worker_id}>
                  <td>{worker.worker_id}</td>
                  <td>{worker.submitted}</td>
                  <td>{worker.accepted}</td>
                  <td>{worker.deferred}</td>
                  <td>{worker.block_reports}</td>
                  <td>{worker.rework_requests}</td>
                  <td>
                    {Object.entries(worker.scope_observations)
                      .map(([observation, count]) => `${observation}: ${count}`)
                      .join(", ") || "—"}
                  </td>
                  <td>
                    {Object.entries(worker.active_time_ms_by_rule)
                      .map(([rule, milliseconds]) => `${milliseconds} ms (${rule})`)
                      .join(", ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {reviewQueue ? (
            <article className="review-queue">
              <h3>复核队列 / Review queue</h3>
              {reviewQueue.length === 0 ? (
                <p>当前没有待复核项目 / No review items</p>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>Task ID</th>
                      <th>类型 / Type</th>
                      <th>冲突原因 / Conflict reasons</th>
                      <th>输入 Revision / Input revisions</th>
                      <th>规则版本 / Rule versions</th>
                      <th>更新时间 / Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reviewQueue.map((item) => (
                      <tr key={item.queue_id ?? `${item.task_id}:${item.input_sha256}`}>
                        <td>{item.task_id}</td>
                        <td>{item.queue_type}</td>
                        <td>{item.conflict_summary.reason_codes.join(", ")}</td>
                        <td>{item.input_revision_ids.join(", ")}</td>
                        <td>
                          {Object.entries(item.rule_versions)
                            .map(([rule, version]) => `${rule}: ${version}`)
                            .join(", ")}
                        </td>
                        <td>
                          <time dateTime={item.updated_at}>
                            {formatLocalTimestamp(item.updated_at, "zh-CN")}
                          </time>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </article>
          ) : null}
        </>
      ) : null}
      {metricSnapshot ? (
        <article className="metric-snapshot">
          <h3>临时快照 / Provisional snapshot</h3>
          <p>该结果是批次时点快照，不是 Final Gold 或 DatasetRelease。</p>
          {metricSnapshot.reused_from_snapshot_id ? (
            <p>复用来源 / Reused from: {metricSnapshot.reused_from_snapshot_id}</p>
          ) : null}
          <dl className="admin-operations-grid">
            <div>
              <dt>状态 / Status</dt>
              <dd>{metricSnapshot.status}</dd>
            </div>
            <div>
              <dt>输入 Revision / Input revisions</dt>
              <dd>{metricSnapshot.input_count}</dd>
            </div>
            <div>
              <dt>支持 / Support</dt>
              <dd>{metricSnapshot.support}</dd>
            </div>
            <div>
              <dt>缺失 / Missing</dt>
              <dd>{metricSnapshot.missing}</dd>
            </div>
            <div>
              <dt>不可评估 / Not evaluable</dt>
              <dd>{metricSnapshot.not_evaluable}</dd>
            </div>
          </dl>
        </article>
      ) : null}
    </section>
  );
}
