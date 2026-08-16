import process from "node:process";

import { expect, test, type Page } from "@playwright/test";

import { loginViaApi, postJson } from "./api-helpers";

const ADMIN_PASSWORD = process.env.PANORAMA_E2E_ADMIN_PASSWORD;
const WORKER_PASSWORD = process.env.PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD;
const WORKER_ID = "00000000-0000-4000-8000-000000000011";

async function login(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("用户名").fill("e2e-admin");
  await page.getByLabel("密码").fill(ADMIN_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page.getByRole("heading", { name: /Batch operations/ })).toBeVisible();
}

test("PAP-AAE-SC-001 reads and refreshes real batch operational counts", async ({ page }) => {
  if (ADMIN_PASSWORD === undefined) throw new Error("Missing administrator E2E credentials");
  await login(page);

  const batchName = `E2E operations ${crypto.randomUUID()}`;
  const created = await page.evaluate(async (name) => {
    const csrfToken = document.cookie
      .split("; ")
      .find((cookie) => cookie.startsWith("csrftoken="))
      ?.slice("csrftoken=".length);
    const response = await fetch("/api/admin/work-batches", {
      body: JSON.stringify({ name }),
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken ?? "" },
      method: "POST",
    });
    return {
      body: (await response.json()) as { batch_id: string },
      status: response.status,
    };
  }, batchName);
  expect(created.status).toBe(201);
  const batchId = created.body.batch_id;

  const initialSnapshot = page.waitForResponse((response) =>
    response.url().endsWith(`/api/admin/work-batches/${batchId}/operations`),
  );
  await page.reload();
  await expect(page.getByRole("heading", { name: /Batch operations/ })).toBeVisible();
  expect((await initialSnapshot).status()).toBe(200);
  await expect(page.getByRole("combobox", { name: /Batch/ })).toHaveValue(batchId);
  await expect(
    page
      .getByText(/Submitted tasks/)
      .locator("..")
      .getByText("0"),
  ).toBeVisible();
  await expect(
    page
      .getByText(/Pending tasks/)
      .locator("..")
      .getByText("0"),
  ).toBeVisible();
  await expect(page.getByText(/Refreshed:/)).toBeVisible();
  await expect(page.getByText(/tier|punishment/i)).not.toBeVisible();

  const refreshed = page.waitForResponse((response) =>
    response.url().endsWith(`/api/admin/work-batches/${batchId}/operations`),
  );
  await page.getByRole("button", { name: /Refresh/ }).click();
  expect((await refreshed).status()).toBe(200);

  const requested = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/admin/work-batches/${batchId}/metric-snapshots`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: /Calculate current snapshot/ }).click();
  expect((await requested).status()).toBe(202);
  await expect(page.getByRole("heading", { name: /Provisional snapshot/ })).toBeVisible();
  await expect(
    page
      .getByText(/Status/)
      .locator("..")
      .getByText("pending"),
  ).toBeVisible();
  await expect(
    page
      .getByText(/Input revisions/)
      .locator("..")
      .getByText("0"),
  ).toBeVisible();
});

test("PAP-AAE-REQ-003 shows a real operational issue in the review queue", async ({ browser }) => {
  if (ADMIN_PASSWORD === undefined || WORKER_PASSWORD === undefined) {
    throw new Error("Missing review queue E2E credentials");
  }
  const adminContext = await browser.newContext();
  const workerContext = await browser.newContext();
  try {
    const adminPage = await adminContext.newPage();
    await login(adminPage);
    const preview = await postJson(adminPage, "/api/admin/media/imports/preview", {
      asset_source_key: `panoramas/e2e/review-queue-${crypto.randomUUID()}`,
      compressed_source_key: "incoming/e2e/review/compressed.jpg",
      create_annotation_round: true,
      high_resolution_source_key: "incoming/e2e/review/high.png",
    });
    expect(preview.status).toBe(200);
    const previewBody = preview.body as { plan_sha256: string; preview_id: string };
    const publication = await postJson(adminPage, "/api/admin/media/imports/publish", {
      expected_plan_sha256: previewBody.plan_sha256,
      preview_id: previewBody.preview_id,
    });
    expect(publication.status).toBeGreaterThanOrEqual(200);
    expect(publication.status).toBeLessThan(300);
    const taskId = (publication.body as { annotation_round: { task_id: string } }).annotation_round
      .task_id;
    const batch = await postJson(adminPage, "/api/admin/work-batches", {
      name: `E2E review queue ${crypto.randomUUID()}`,
    });
    expect(batch.status).toBe(201);
    const batchId = (batch.body as { batch_id: string }).batch_id;
    const assignment = await postJson(adminPage, `/api/admin/work-batches/${batchId}/assignments`, {
      task_id: taskId,
      worker_id: WORKER_ID,
    });
    expect(assignment.status).toBe(201);
    const assignmentId = (assignment.body as { assignment_id: string }).assignment_id;

    const workerPage = await workerContext.newPage();
    await loginViaApi(workerPage, "e2e-review-worker", WORKER_PASSWORD);
    await workerPage.reload();
    await expect(workerPage.getByText("工作区可编辑。")).toBeVisible();
    const draftResponse = workerPage.waitForResponse(
      (response) =>
        response.url().includes(`/api/worker/assignments/${assignmentId}/draft?`) &&
        response.request().method() === "GET",
    );
    await workerPage.getByRole("button", { name: `打开 ${taskId}` }).click();
    const initialDraft = await draftResponse;
    expect(initialDraft.status()).toBe(200);
    const draftVersion = ((await initialDraft.json()) as { draft_version: number }).draft_version;
    const tabId = new URL(initialDraft.url()).searchParams.get("tab_id");
    expect(tabId).toMatch(/^[0-9a-f-]{36}$/);
    const rejectedSave = await workerPage.evaluate(
      async ({ id, tabId, version }) => {
        const csrfToken = document.cookie
          .split("; ")
          .find((cookie) => cookie.startsWith("csrftoken="))
          ?.slice("csrftoken=".length);
        const response = await fetch(`/api/worker/assignments/${id}/draft`, {
          body: JSON.stringify({ expected_draft_version: version, state: 42, tab_id: tabId }),
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken ?? "" },
          method: "PUT",
        });
        return { body: await response.json(), status: response.status };
      },
      { id: assignmentId, tabId, version: draftVersion },
    );
    expect(rejectedSave).toMatchObject({
      body: { error: { code: "annotation_field_type_invalid" } },
      status: 400,
    });

    await adminPage.reload();
    await expect(adminPage.getByRole("heading", { name: /Batch operations/ })).toBeVisible();
    const queueResponse = adminPage.waitForResponse((response) =>
      response.url().endsWith(`/api/admin/work-batches/${batchId}/review-queue`),
    );
    await adminPage.getByRole("combobox", { name: /Batch/ }).selectOption(batchId);
    expect((await queueResponse).status()).toBe(200);
    await expect(adminPage.getByRole("combobox", { name: /Batch/ })).toHaveValue(batchId);
    await expect(adminPage.getByRole("heading", { name: /Review queue/ })).toBeVisible();
    await expect(adminPage.getByText("operational_issue")).toBeVisible();
    await expect(adminPage.getByText("annotation_field_type_invalid")).toBeVisible();
    await expect(adminPage.getByRole("cell", { name: taskId })).toBeVisible();
  } finally {
    await Promise.all([adminContext.close(), workerContext.close()]);
  }
});
