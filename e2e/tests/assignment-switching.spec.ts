import process from "node:process";

import { expect, test, type Page } from "@playwright/test";

const ADMIN_PASSWORD = process.env.PANORAMA_E2E_ADMIN_PASSWORD;
const WORKER_PASSWORD = process.env.PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD;
const WORKER_ID = "00000000-0000-4000-8000-000000000007";

async function postJson(page: Page, endpoint: string, body: object) {
  return page.evaluate(
    async ({ endpoint: requestEndpoint, payload }) => {
      const csrfToken = document.cookie
        .split("; ")
        .find((cookie) => cookie.startsWith("csrftoken="))
        ?.slice("csrftoken=".length);
      const response = await fetch(requestEndpoint, {
        body: JSON.stringify(payload),
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken ?? "" },
        method: "POST",
      });
      return { body: await response.json(), status: response.status };
    },
    { endpoint, payload: body },
  );
}

async function login(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/");
  await page.evaluate(async () => {
    await fetch("/api/auth/csrf", { credentials: "same-origin" });
  });
  const response = await postJson(page, "/api/auth/login", { password, username });
  expect(response.status).toBe(200);
}

async function createAnnotationRound(page: Page): Promise<string> {
  const preview = await postJson(page, "/api/admin/media/imports/preview", {
    asset_source_key: "panoramas/e2e/assignment",
    compressed_source_key: "incoming/e2e/assignment/compressed.jpg",
    create_annotation_round: true,
    high_resolution_source_key: "incoming/e2e/assignment/high.png",
  });
  expect(preview.status).toBe(200);
  const previewBody = preview.body as { plan_sha256: string; preview_id: string };
  const publication = await postJson(page, "/api/admin/media/imports/publish", {
    expected_plan_sha256: previewBody.plan_sha256,
    preview_id: previewBody.preview_id,
  });
  expect(publication.status).toBeGreaterThanOrEqual(200);
  expect(publication.status).toBeLessThan(300);
  return (publication.body as { annotation_round: { task_id: string } }).annotation_round.task_id;
}

test("tasks 4.3/4.4 switch real owned Assignments", async ({ browser }) => {
  if (ADMIN_PASSWORD === undefined || WORKER_PASSWORD === undefined) {
    throw new Error("Missing assignment E2E credentials");
  }
  const adminContext = await browser.newContext();
  const workerContext = await browser.newContext();
  try {
    const adminPage = await adminContext.newPage();
    await login(adminPage, "e2e-admin", ADMIN_PASSWORD);
    const firstTaskId = await createAnnotationRound(adminPage);
    const secondTaskId = await createAnnotationRound(adminPage);
    expect(secondTaskId).not.toBe(firstTaskId);

    const batch = await postJson(adminPage, "/api/admin/work-batches", {
      name: "E2E Assignment batch",
    });
    expect(batch.status).toBe(201);
    const batchId = (batch.body as { batch_id: string }).batch_id;
    for (const taskId of [firstTaskId, secondTaskId]) {
      const assignment = await postJson(
        adminPage,
        `/api/admin/work-batches/${batchId}/assignments`,
        { task_id: taskId, worker_id: WORKER_ID },
      );
      expect(assignment.status).toBe(201);
    }

    const workerPage = await workerContext.newPage();
    await login(workerPage, "e2e-assignment-worker", WORKER_PASSWORD);
    await workerPage.reload();
    await expect(workerPage.getByText("工作区可编辑。")).toBeVisible();
    await expect(workerPage.getByRole("heading", { name: firstTaskId })).toBeVisible();
    await expect(workerPage.getByRole("heading", { name: secondTaskId })).toBeVisible();

    await workerPage.getByRole("button", { name: `暂缓 ${firstTaskId}` }).click();
    await expect(workerPage.getByText("队列：deferred")).toBeVisible();
    await workerPage.getByRole("button", { name: `打开 ${secondTaskId}` }).click();
    await expect(workerPage.getByText(`当前 Assignment：${secondTaskId}`)).toBeVisible();
    await expect(workerPage.getByText("工作：in_progress")).toBeVisible();
  } finally {
    await Promise.all([adminContext.close(), workerContext.close()]);
  }
});
