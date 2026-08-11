import { type FormEvent, useEffect, useState } from "react";

import { AdminOperations } from "./admin/AdminOperations";
import { apiFetch } from "./api";
import { AssignmentSwitcher } from "./assignment/AssignmentSwitcher";
import { MediaImportWizard } from "./media/MediaImportWizard";
import { DevicePreflightGate } from "./platform/DevicePreflightGate";
import { PredictionImportWizard } from "./prediction/PredictionImportWizard";
import { DataNoticeGate } from "./privacy/DataNoticeGate";
import { WorkerWorkspaceGate } from "./workspace/WorkerWorkspaceGate";

type SessionState =
  | { authenticated: false }
  | { authenticated: true; must_change_password: boolean; role: "admin" | "worker" };

function LoginForm({ onComplete }: { onComplete: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch("/api/auth/login", {
        body: JSON.stringify({ password, username }),
        method: "POST",
      });
      if (!response.ok) {
        setError(response.status === 401 ? "用户名或密码错误。" : "无法登录，请重试。");
        return;
      }
      onComplete();
    } catch {
      setError("无法登录，请重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="login-heading" className="auth-panel">
      <h2 id="login-heading">登录</h2>
      <form onSubmit={(event) => void submit(event)}>
        <label>
          用户名
          <input
            autoComplete="username"
            onChange={(event) => setUsername(event.target.value)}
            required
            value={username}
          />
        </label>
        <label>
          密码
          <input
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
            required
            type="password"
            value={password}
          />
        </label>
        <button disabled={busy} type="submit">
          {busy ? "登录中…" : "登录"}
        </button>
      </form>
      {error ? <p role="alert">{error}</p> : null}
    </section>
  );
}

function ChangePasswordForm({ onComplete }: { onComplete: () => void }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch("/api/auth/change-password", {
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
        method: "POST",
      });
      if (!response.ok) {
        setError("当前密码或新密码不符合要求。");
        return;
      }
      onComplete();
    } catch {
      setError("无法修改密码，请重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="change-password-heading" className="auth-panel">
      <h2 id="change-password-heading">首次修改密码</h2>
      <p>临时密码只能用于首次登录。新密码至少 12 个字符。</p>
      <form onSubmit={(event) => void submit(event)}>
        <label>
          当前密码
          <input
            autoComplete="current-password"
            onChange={(event) => setCurrentPassword(event.target.value)}
            required
            type="password"
            value={currentPassword}
          />
        </label>
        <label>
          新密码
          <input
            autoComplete="new-password"
            minLength={12}
            onChange={(event) => setNewPassword(event.target.value)}
            required
            type="password"
            value={newPassword}
          />
        </label>
        <label>
          确认新密码
          <input
            autoComplete="new-password"
            minLength={12}
            onChange={(event) => setConfirmation(event.target.value)}
            required
            type="password"
            value={confirmation}
          />
        </label>
        <button disabled={busy} type="submit">
          {busy ? "修改中…" : "修改密码"}
        </button>
      </form>
      {error ? <p role="alert">{error}</p> : null}
    </section>
  );
}

export default function App() {
  const [session, setSession] = useState<SessionState | null>(null);
  const [sessionError, setSessionError] = useState(false);
  const [sessionRetry, setSessionRetry] = useState(0);

  useEffect(() => {
    let active = true;
    setSession(null);
    setSessionError(false);
    void apiFetch("/api/auth/session")
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("session unavailable");
        }
        const payload = (await response.json()) as SessionState;
        if (active) {
          setSession(payload);
        }
      })
      .catch(() => {
        if (active) {
          setSessionError(true);
        }
      });
    return () => {
      active = false;
    };
  }, [sessionRetry]);

  const workerSession = session?.authenticated && session.role === "worker";

  return (
    <main className={`app-shell${workerSession ? " worker-shell" : ""}`}>
      <p className="eyebrow">V1 · 独立平台</p>
      <h1>Panorama Annotation Platform</h1>
      <p>OpenSpec 实施中</p>
      {session === null && !sessionError ? <p>正在读取会话…</p> : null}
      {sessionError ? (
        <p role="alert">
          无法读取会话。
          <button onClick={() => setSessionRetry((value) => value + 1)} type="button">
            重试
          </button>
        </p>
      ) : null}
      {session?.authenticated === false ? (
        <LoginForm onComplete={() => setSessionRetry((value) => value + 1)} />
      ) : null}
      {session?.authenticated && session.role === "admin" ? (
        <>
          <AdminOperations />
          <MediaImportWizard />
          <PredictionImportWizard />
        </>
      ) : null}
      {session?.authenticated && session.role === "worker" && !session.must_change_password ? (
        <DataNoticeGate>
          <DevicePreflightGate>
            <WorkerWorkspaceGate>
              {({ online, tabId, writable }) => (
                <AssignmentSwitcher online={online} tabId={tabId} writable={writable} />
              )}
            </WorkerWorkspaceGate>
          </DevicePreflightGate>
        </DataNoticeGate>
      ) : null}
      {session?.authenticated && session.role === "worker" && session.must_change_password ? (
        <ChangePasswordForm onComplete={() => setSessionRetry((value) => value + 1)} />
      ) : null}
    </main>
  );
}
