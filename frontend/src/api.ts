const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

function csrfToken(): string | undefined {
  return document.cookie
    .split("; ")
    .find((cookie) => cookie.startsWith("csrftoken="))
    ?.slice("csrftoken=".length);
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);

  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (!SAFE_METHODS.has(method)) {
    if (csrfToken() === undefined) {
      const bootstrap = await fetch("/api/auth/csrf", {
        credentials: "same-origin",
        ...(init.signal === undefined ? {} : { signal: init.signal }),
      });
      if (!bootstrap.ok) {
        throw new Error("CSRF bootstrap failed");
      }
    }
    const token = csrfToken();
    if (token === undefined) {
      throw new Error("CSRF cookie is unavailable");
    }
    headers.set("X-CSRFToken", token);
  }

  return fetch(input, { ...init, credentials: "same-origin", headers });
}
