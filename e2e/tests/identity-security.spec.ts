import process from "node:process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

const BACKEND_URL = process.env.PANORAMA_E2E_BACKEND_URL ?? "http://127.0.0.1:8000";

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined) {
    throw new Error(`Missing required E2E environment variable: ${name}`);
  }
  return value;
}

const WORKER = {
  id: "00000000-0000-4000-8000-000000000001",
  changedPassword: requiredEnvironment("PANORAMA_E2E_WORKER_CHANGED_PASSWORD"),
  initialPassword: requiredEnvironment("PANORAMA_E2E_WORKER_INITIAL_PASSWORD"),
  username: "e2e-worker",
};
const ADMIN = {
  password: requiredEnvironment("PANORAMA_E2E_ADMIN_PASSWORD"),
  username: "e2e-admin",
};

type ApiResponse = {
  body: unknown;
  status: number;
};

async function openBackendPage(context: BrowserContext): Promise<Page> {
  const page = await context.newPage();
  const response = await page.goto(`${BACKEND_URL}/api/auth/csrf`);
  if (!response?.ok()) {
    throw new Error("CSRF bootstrap failed");
  }
  return page;
}

async function request(
  page: Page,
  path: string,
  method: "GET" | "POST",
  body?: Record<string, string>,
): Promise<ApiResponse> {
  return page.evaluate(
    async ({ body: payload, method: requestMethod, path: requestPath }) => {
      const csrfToken = document.cookie
        .split("; ")
        .find((cookie) => cookie.startsWith("csrftoken="))
        ?.slice("csrftoken=".length);
      const response = await fetch(requestPath, {
        body: payload === undefined ? undefined : JSON.stringify(payload),
        credentials: "include",
        headers:
          payload === undefined
            ? undefined
            : { "Content-Type": "application/json", "X-CSRFToken": csrfToken ?? "" },
        method: requestMethod,
      });
      const responseText = await response.text();
      return {
        body: response.headers.get("content-type")?.includes("application/json")
          ? JSON.parse(responseText)
          : responseText || null,
        status: response.status,
      };
    },
    { body, method, path },
  );
}

async function login(page: Page, username: string, password: string): Promise<ApiResponse> {
  return request(page, "/api/auth/login", "POST", { password, username });
}

test("browser bootstraps CSRF through the Vite API proxy", async ({ page }) => {
  await page.goto("/");

  await expect(request(page, "/api/auth/csrf", "GET")).resolves.toEqual({
    body: { csrf_ready: true },
    status: 200,
  });
  await expect(login(page, ADMIN.username, ADMIN.password)).resolves.toEqual({
    body: { must_change_password: false, workspace_access: false },
    status: 200,
  });
  const createdWorker = await request(page, "/api/admin/workers", "POST", {
    username: "e2e-csrf-worker",
  });
  expect(createdWorker.status).toBe(201);
  expect(createdWorker.body).toMatchObject({ username: "e2e-csrf-worker" });
});

test("PAP-IAM-SC-011 keeps browser sessions HttpOnly and revokes worker sessions", async ({
  browser,
}) => {
  const firstWorkerContext = await browser.newContext();
  const secondWorkerContext = await browser.newContext();
  const administratorContext = await browser.newContext();

  try {
    const firstWorkerPage = await openBackendPage(firstWorkerContext);
    const secondWorkerPage = await openBackendPage(secondWorkerContext);
    const administratorPage = await openBackendPage(administratorContext);

    await expect(login(firstWorkerPage, WORKER.username, WORKER.initialPassword)).resolves.toEqual({
      body: { must_change_password: false, workspace_access: true },
      status: 200,
    });
    await expect(login(secondWorkerPage, WORKER.username, WORKER.initialPassword)).resolves.toEqual(
      {
        body: { must_change_password: false, workspace_access: true },
        status: 200,
      },
    );

    const sessionCookie = (await firstWorkerContext.cookies(BACKEND_URL)).find(
      (cookie) => cookie.name === "sessionid",
    );
    expect(sessionCookie).toMatchObject({ httpOnly: true });
    expect(
      await firstWorkerPage.evaluate(() => ({
        localStorage: Array.from({ length: localStorage.length }, (_, index) =>
          localStorage.key(index),
        ),
        sessionStorage: Array.from({ length: sessionStorage.length }, (_, index) =>
          sessionStorage.key(index),
        ),
      })),
    ).toEqual({ localStorage: [], sessionStorage: [] });

    await expect(
      request(firstWorkerPage, "/api/auth/change-password", "POST", {
        current_password: WORKER.initialPassword,
        new_password: WORKER.changedPassword,
      }),
    ).resolves.toEqual({ body: null, status: 204 });
    await expect(request(firstWorkerPage, "/api/workspace/session", "GET")).resolves.toEqual({
      body: { workspace_access: true },
      status: 200,
    });
    await expect(request(secondWorkerPage, "/api/workspace/session", "GET")).resolves.toEqual({
      body: { error: { code: "authentication_required" } },
      status: 401,
    });

    await expect(login(administratorPage, ADMIN.username, ADMIN.password)).resolves.toEqual({
      body: { must_change_password: false, workspace_access: false },
      status: 200,
    });
    await expect(
      request(administratorPage, `/api/admin/workers/${WORKER.id}/disable`, "POST", {}),
    ).resolves.toEqual({ body: null, status: 204 });
    await expect(request(firstWorkerPage, "/api/workspace/session", "GET")).resolves.toEqual({
      body: { error: { code: "authentication_required" } },
      status: 401,
    });
  } finally {
    await Promise.all([
      firstWorkerContext.close(),
      secondWorkerContext.close(),
      administratorContext.close(),
    ]);
  }
});
