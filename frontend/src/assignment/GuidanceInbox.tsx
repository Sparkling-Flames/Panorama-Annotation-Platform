import { useState } from "react";

import { apiFetch } from "../api";

type GuidanceEvent = {
  acknowledged_at: string | null;
  administrator_id: string;
  assignment_id: string;
  batch_id: string;
  category: string;
  channel: string;
  created_at: string;
  feedback_draft_cycle_id: string | null;
  guidance_id: string;
  revision_id: string | null;
  summary: string;
  task_id: string;
  worker_id: string;
};

export function GuidanceInbox() {
  const [events, setEvents] = useState<GuidanceEvent[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState(false);

  async function load(): Promise<void> {
    setError(false);
    try {
      const response = await apiFetch("/api/worker/guidance-events");
      if (!response.ok) throw new Error("guidance unavailable");
      const payload = (await response.json()) as { events: GuidanceEvent[] };
      setEvents(payload.events);
    } catch {
      setError(true);
    }
  }

  async function acknowledge(event: GuidanceEvent): Promise<void> {
    setBusyId(event.guidance_id);
    setError(false);
    try {
      const response = await apiFetch(
        `/api/worker/guidance-events/${event.guidance_id}/acknowledge`,
        { body: "{}", method: "POST" },
      );
      if (!response.ok) throw new Error("acknowledgement rejected");
      const updated = (await response.json()) as GuidanceEvent;
      setEvents(
        (current) =>
          current?.map((item) => (item.guidance_id === updated.guidance_id ? updated : item)) ?? [],
      );
    } catch {
      setError(true);
    } finally {
      setBusyId(null);
    }
  }

  if (events === null) {
    return (
      <section aria-label="指导 / Guidance">
        <button onClick={() => void load()} type="button">
          查看指导 / View guidance
        </button>
        {error ? <p role="alert">无法读取指导 / Guidance unavailable</p> : null}
      </section>
    );
  }

  return (
    <section aria-label="指导 / Guidance">
      <h3>指导 / Guidance</h3>
      {events.length === 0 ? <p>暂无指导 / No guidance</p> : null}
      <ul>
        {events.map((event) => (
          <li key={event.guidance_id}>
            <p>{event.summary}</p>
            <p>类别 / Category: {event.category}</p>
            <p>渠道 / Channel: {event.channel}</p>
            <p>管理员 / Administrator: {event.administrator_id}</p>
            {event.acknowledged_at === null ? (
              <button
                disabled={busyId !== null}
                onClick={() => void acknowledge(event)}
                type="button"
              >
                确认已阅读 / Acknowledge
              </button>
            ) : (
              <p>已确认 / Acknowledged</p>
            )}
          </li>
        ))}
      </ul>
      {error ? <p role="alert">无法更新指导 / Guidance update failed</p> : null}
    </section>
  );
}
