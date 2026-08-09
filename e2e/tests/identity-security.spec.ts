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

async function storageContents(page: Page) {
  return page.evaluate(() => ({
    localStorage: Object.fromEntries(
      Array.from({ length: localStorage.length }, (_, index) => {
        const key = localStorage.key(index) ?? "";
        return [key, localStorage.getItem(key)];
      }),
    ),
    sessionStorage: Object.fromEntries(
      Array.from({ length: sessionStorage.length }, (_, index) => {
        const key = sessionStorage.key(index) ?? "";
        return [key, sessionStorage.getItem(key)];
      }),
    ),
  }));
}

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
  await request(page, "/api/auth/csrf", "GET");
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

test("PAP-IAM-SC-003 requires the temporary password to be changed before workspace access", async ({
  browser,
}) => {
  const administratorContext = await browser.newContext();
  const workerContext = await browser.newContext();

  try {
    const administratorPage = await administratorContext.newPage();
    await administratorPage.goto("/");
    await expect(login(administratorPage, ADMIN.username, ADMIN.password)).resolves.toEqual({
      body: { must_change_password: false, workspace_access: false },
      status: 200,
    });
    const createdWorker = await request(administratorPage, "/api/admin/workers", "POST", {
      username: "e2e-first-change-worker",
    });
    expect(createdWorker.status).toBe(201);
    const worker = createdWorker.body as {
      temporary_password: string;
      worker_id: string;
    };
    expect(worker.worker_id).toMatch(/^[0-9a-f-]{36}$/);

    const workerPage = await workerContext.newPage();
    await workerPage.goto("/");
    await expect(
      login(workerPage, "e2e-first-change-worker", worker.temporary_password),
    ).resolves.toEqual({
      body: { must_change_password: true, workspace_access: false },
      status: 200,
    });
    await workerPage.reload();
    await expect(workerPage.getByText("请先修改临时密码再进入工作区。")).toBeVisible();
    await expect(request(workerPage, "/api/workspace/session", "GET")).resolves.toEqual({
      body: { error: { code: "password_change_required" } },
      status: 403,
    });

    await expect(
      request(workerPage, "/api/auth/change-password", "POST", {
        current_password: worker.temporary_password,
        new_password: WORKER.changedPassword,
      }),
    ).resolves.toEqual({ body: null, status: 204 });
    await expect(request(workerPage, "/api/workspace/session", "GET")).resolves.toEqual({
      body: { workspace_access: true },
      status: 200,
    });
    await workerPage.reload();
    await expect(workerPage.getByRole("region", { name: "工人工作区" })).toBeVisible();
    await expect(workerPage.getByText("请先修改临时密码再进入工作区。")).not.toBeVisible();

    const sessionCookie = (await workerContext.cookies()).find(
      (cookie) => cookie.name === "sessionid",
    );
    expect(sessionCookie).toMatchObject({ httpOnly: true });
    expect(await storageContents(workerPage)).toEqual({ localStorage: {}, sessionStorage: {} });
  } finally {
    await Promise.all([administratorContext.close(), workerContext.close()]);
  }
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
    expect(await storageContents(firstWorkerPage)).toEqual({
      localStorage: {},
      sessionStorage: {},
    });

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
      request(administratorPage, `/api/admin/workers/${WORKER.id}/revoke-sessions`, "POST", {}),
    ).resolves.toEqual({ body: null, status: 204 });
    await expect(request(firstWorkerPage, "/api/workspace/session", "GET")).resolves.toEqual({
      body: { error: { code: "authentication_required" } },
      status: 401,
    });
    await expect(login(firstWorkerPage, WORKER.username, WORKER.changedPassword)).resolves.toEqual({
      body: { must_change_password: false, workspace_access: true },
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

test("PAP-IAM-REQ-003 rejects unauthenticated and worker access to the admin media flow", async ({
  browser,
}) => {
  const anonymousContext = await browser.newContext();
  const workerContext = await browser.newContext();

  try {
    const anonymousPage = await anonymousContext.newPage();
    await anonymousPage.goto("/");
    await expect(
      anonymousPage.getByRole("heading", { name: "管理员 COS 媒体导入" }),
    ).not.toBeVisible();
    await expect(
      request(anonymousPage, "/api/admin/media/candidates?prefix=incoming/e2e", "GET"),
    ).resolves.toEqual({
      body: { error: { code: "authentication_required" } },
      status: 401,
    });

    const workerPage = await workerContext.newPage();
    await workerPage.goto("/");
    await expect(
      login(
        workerPage,
        "e2e-media-role-worker",
        requiredEnvironment("PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD"),
      ),
    ).resolves.toEqual({
      body: { must_change_password: false, workspace_access: true },
      status: 200,
    });
    await workerPage.reload();
    await expect(
      workerPage.getByRole("heading", { name: "管理员 COS 媒体导入" }),
    ).not.toBeVisible();
    await expect(
      request(workerPage, "/api/admin/media/candidates?prefix=incoming/e2e", "GET"),
    ).resolves.toEqual({
      body: { error: { code: "admin_required" } },
      status: 403,
    });
    expect(await storageContents(workerPage)).toEqual({ localStorage: {}, sessionStorage: {} });
  } finally {
    await Promise.all([anonymousContext.close(), workerContext.close()]);
  }
});
