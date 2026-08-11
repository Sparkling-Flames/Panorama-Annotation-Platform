import { type ReactNode, useEffect, useState } from "react";

import { apiFetch } from "../api";

type NoticeCopy = { collected_data: string[]; summary: string; title: string };
type Notice = {
  accepted: boolean;
  accepted_at: string | null;
  collected_data: string[];
  copy: { en: NoticeCopy; "zh-CN": NoticeCopy };
  notice_version: string;
};

export function DataNoticeGate({ children }: { children: ReactNode }) {
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    void apiFetch("/api/privacy/notice", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("notice unavailable");
        const payload = (await response.json()) as Notice;
        setNotice(payload);
        setAccepted(payload.accepted);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, [retry]);

  useEffect(() => {
    const refresh = () => setRetry((value) => value + 1);
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, []);

  async function accept(): Promise<void> {
    if (notice === null || busy) return;
    setBusy(true);
    setError(false);
    try {
      const response = await apiFetch("/api/privacy/notice/accept", {
        body: JSON.stringify({ notice_version: notice.notice_version }),
        method: "POST",
      });
      if (!response.ok) throw new Error("acceptance rejected");
      setAccepted(true);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  if (accepted) return children;
  if (notice === null) {
    return (
      <p role={error ? "alert" : "status"}>
        {error
          ? "无法读取数据告知 / Data notice unavailable"
          : "正在读取数据告知… / Loading data notice…"}
        {error ? (
          <button onClick={() => setRetry((value) => value + 1)}>重试 / Retry</button>
        ) : null}
      </p>
    );
  }
  return (
    <section aria-labelledby="data-notice-heading">
      <h2 id="data-notice-heading">
        {notice.copy["zh-CN"].title} / {notice.copy.en.title}
      </h2>
      <p>{notice.copy["zh-CN"].summary}</p>
      <ul>
        {notice.copy["zh-CN"].collected_data.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p>{notice.copy.en.summary}</p>
      <ul>
        {notice.copy.en.collected_data.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p>notice_version: {notice.notice_version}</p>
      <button disabled={busy} onClick={() => void accept()} type="button">
        {busy ? "确认中… / Accepting…" : "确认并继续 / Accept and continue"}
      </button>
      {error ? <p role="alert">无法保存确认 / Acceptance unavailable</p> : null}
    </section>
  );
}
