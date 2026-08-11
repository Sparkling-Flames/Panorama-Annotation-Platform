import process from "node:process";

import { expect, test, type Page } from "@playwright/test";

import { loginViaApi as login, postJson } from "./api-helpers";

const ADMIN_PASSWORD = process.env.PANORAMA_E2E_ADMIN_PASSWORD;
const WORKER_PASSWORD = process.env.PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD;
const WORKER_ID = "00000000-0000-4000-8000-000000000007";
const REVISION_WORKER_ID = "00000000-0000-4000-8000-000000000009";
const OFFLINE_WORKER_ID = "00000000-0000-4000-8000-000000000010";

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

async function indexedDbContents(page: Page) {
  return page.evaluate(async () => {
    const contents: unknown[] = [];
    for (const { name } of await indexedDB.databases()) {
      if (name === undefined) continue;
      const database = await new Promise<IDBDatabase>((resolve, reject) => {
        const request = indexedDB.open(name);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      for (const storeName of database.objectStoreNames) {
        contents.push(
          ...(await new Promise<unknown[]>((resolve, reject) => {
            const request = database.transaction(storeName).objectStore(storeName).getAll();
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          })),
        );
      }
      database.close();
    }
    return contents;
  });
}

test("PAP-TBA-SC-006 PAP-TBA-SC-007 switches real owned Assignments", async ({ browser }) => {
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

    await workerPage.getByRole("button", { name: `打开 ${firstTaskId}` }).click();
    await expect(workerPage.getByText(`当前 Assignment：${firstTaskId}`)).toBeVisible();
    await expect(
      workerPage.getByRole("listitem").filter({ hasText: firstTaskId }).getByText("队列：ready"),
    ).toBeVisible();
  } finally {
    await Promise.all([adminContext.close(), workerContext.close()]);
  }
});

test("PAP-DRR-SC-001 PAP-DRR-SC-003 PAP-DRR-SC-005 PAP-ANN-SC-017 PAP-ANN-SC-022 PAP-AOF-REQ-002 saves and revises canonical observations", async ({
  browser,
}) => {
  if (ADMIN_PASSWORD === undefined || WORKER_PASSWORD === undefined) {
    throw new Error("Missing revision E2E credentials");
  }
  const adminContext = await browser.newContext();
  const workerContext = await browser.newContext();
  try {
    const adminPage = await adminContext.newPage();
    await login(adminPage, "e2e-admin", ADMIN_PASSWORD);
    const taskId = await createAnnotationRound(adminPage);
    const batch = await postJson(adminPage, "/api/admin/work-batches", {
      name: "E2E revision batch",
    });
    expect(batch.status).toBe(201);
    const assignment = await postJson(
      adminPage,
      `/api/admin/work-batches/${(batch.body as { batch_id: string }).batch_id}/assignments`,
      { task_id: taskId, worker_id: REVISION_WORKER_ID },
    );
    expect(assignment.status).toBe(201);

    const workerPage = await workerContext.newPage();
    const activityStatuses: number[] = [];
    workerPage.on("response", (response) => {
      if (response.url().endsWith("/api/worker/activity-events")) {
        activityStatuses.push(response.status());
      }
    });
    await login(workerPage, "e2e-revision-worker", WORKER_PASSWORD);
    await workerPage.reload();
    await expect(workerPage.getByText("工作区可编辑。")).toBeVisible();
    await workerPage.getByRole("button", { name: `打开 ${taskId}` }).click();

    const activityResponse = workerPage.waitForResponse(
      (response) =>
        response.url().endsWith("/api/worker/activity-events") &&
        response.request().method() === "POST",
    );
    await workerPage.getByLabel("Scope / 范围判断").selectOption("needs_scope_review");
    const acceptedActivity = await activityResponse;
    expect(acceptedActivity.status()).toBe(201);
    expect(await acceptedActivity.json()).toMatchObject({
      event_id: expect.any(String),
      server_received_at: expect.any(String),
    });
    expect(acceptedActivity.request().postDataJSON()).not.toHaveProperty("pointer_coordinates");
    await workerPage.getByLabel("证据不足").check();
    await workerPage.getByLabel("Geometry attempt / 几何完成度").selectOption("partial");
    await workerPage.getByLabel("非常简单").check();

    await workerPage.getByRole("button", { name: "添加角点对" }).click();
    const canvas = workerPage.getByLabel("全景规范化坐标编辑区");
    await canvas.click({ position: { x: 180, y: 80 } });
    await canvas.click({ position: { x: 210, y: 320 } });
    await expect(workerPage.getByLabel("第 1 对顶点水平坐标")).toBeVisible();

    await workerPage.getByRole("button", { name: "添加 Portal" }).click();
    await workerPage.getByLabel("Portal 类型").selectOption("door");
    await workerPage.getByLabel("Portal 证据状态").selectOption("direct_visible");
    for (const [label, value] of [
      ["Portal 左上 u", "0.2"],
      ["Portal 左上 v", "0.2"],
      ["Portal 右上 u", "0.3"],
      ["Portal 右上 v", "0.2"],
      ["Portal 左下 u", "0.2"],
      ["Portal 左下 v", "0.8"],
      ["Portal 右下 u", "0.3"],
      ["Portal 右下 v", "0.8"],
    ] as const) {
      await workerPage.getByLabel(label).fill(value);
    }
    await workerPage.getByLabel("Portal host edge").selectOption({ index: 1 });
    const portalSave = workerPage.waitForResponse(
      (response) => response.url().endsWith("/draft") && response.request().method() === "PUT",
    );
    await workerPage.getByRole("button", { name: "保存 Portal" }).click();
    expect((await portalSave).status()).toBe(200);

    await expect(workerPage.getByText("已保存")).toBeVisible();
    await expect(workerPage.getByLabel("本地 3D 预览（仅供参考）")).toHaveAttribute(
      "data-authority",
      "informational",
    );
    const submitResponse = workerPage.waitForResponse(
      (response) => response.url().endsWith("/submit") && response.request().method() === "POST",
    );
    await workerPage.getByRole("button", { name: "提交 Revision" }).click();
    const firstSubmission = await submitResponse;
    expect(firstSubmission.status()).toBe(201);
    expect(firstSubmission.request().postDataJSON()).toMatchObject({
      client_build_sha: expect.any(String),
      interaction_contract_version: "annotation-interaction-v1",
      locale: "zh-CN",
      viewer_version: "annotation-viewer-v1",
    });
    const revisionId = ((await firstSubmission.json()) as { revision_id: string }).revision_id;
    expect(revisionId).toMatch(/^[0-9a-f-]{36}$/);
    await expect(workerPage.getByRole("button", { name: `修订 ${taskId}` })).toBeVisible();

    const revision = await adminPage.evaluate(async (id) => {
      const response = await fetch(`/api/admin/revisions/${id}`, { credentials: "same-origin" });
      return { body: await response.json(), status: response.status };
    }, revisionId);
    expect(revision.status).toBe(200);
    expect(revision.body).toMatchObject({
      locale: "zh-CN",
      revision_no: 1,
      state: {
        geometry_attempt_status: "partial",
        scope_reason_codes: ["insufficient_evidence"],
        worker_scope_observation: "needs_scope_review",
      },
    });
    expect(revision.body.state.pairs).toHaveLength(1);
    expect(revision.body.state.portals).toHaveLength(1);
    expect(revision.body.state.portals[0]).toMatchObject({
      evidence_status: "direct_visible",
      kind: "door",
    });

    await workerPage.getByRole("button", { name: `修订 ${taskId}` }).click();
    const topU = workerPage.getByLabel("第 1 对顶点水平坐标");
    await expect(topU).toBeVisible();
    await expect(workerPage.getByText("Portal 1: door / direct_visible")).toBeVisible();
    await topU.fill("0.25");
    await expect(workerPage.getByText("已保存")).toBeVisible();
    const resubmitResponse = workerPage.waitForResponse(
      (response) => response.url().endsWith("/submit") && response.request().method() === "POST",
    );
    await workerPage.getByRole("button", { name: "提交 Revision" }).click();
    const secondSubmission = await resubmitResponse;
    expect(secondSubmission.status()).toBe(201);
    const secondRevisionId = ((await secondSubmission.json()) as { revision_id: string })
      .revision_id;
    expect(secondRevisionId).toMatch(/^[0-9a-f-]{36}$/);
    const secondRevision = await adminPage.evaluate(async (id) => {
      const response = await fetch(`/api/admin/revisions/${id}`, { credentials: "same-origin" });
      return { body: await response.json(), status: response.status };
    }, secondRevisionId);
    expect(secondRevision.status).toBe(200);
    expect(secondRevision.body).toMatchObject({
      revision_no: 2,
      state: { pairs: [{ top: { u: 0.25 } }] },
    });
    expect(activityStatuses.filter((status) => status >= 500)).toEqual([]);
  } finally {
    await Promise.all([adminContext.close(), workerContext.close()]);
  }
});

test("PAP-AOF-SC-007 keeps a loaded Assignment editable offline and persists recovery queues", async ({
  browser,
}) => {
  if (ADMIN_PASSWORD === undefined || WORKER_PASSWORD === undefined) {
    throw new Error("Missing offline E2E credentials");
  }
  const adminContext = await browser.newContext();
  const workerContext = await browser.newContext();
  try {
    const adminPage = await adminContext.newPage();
    await login(adminPage, "e2e-admin", ADMIN_PASSWORD);
    const taskId = await createAnnotationRound(adminPage);
    const batch = await postJson(adminPage, "/api/admin/work-batches", {
      name: "E2E offline batch",
    });
    expect(batch.status).toBe(201);
    const assignment = await postJson(
      adminPage,
      `/api/admin/work-batches/${(batch.body as { batch_id: string }).batch_id}/assignments`,
      { task_id: taskId, worker_id: OFFLINE_WORKER_ID },
    );
    expect(assignment.status).toBe(201);
    const assignmentId = (assignment.body as { assignment_id: string }).assignment_id;

    const workerPage = await workerContext.newPage();
    await login(workerPage, "e2e-offline-worker", WORKER_PASSWORD);
    await workerPage.reload();
    await expect(workerPage.getByText("工作区可编辑。")).toBeVisible();
    await workerPage.getByRole("button", { name: `打开 ${taskId}` }).click();
    const image = workerPage.getByAltText("当前任务全景图（压缩）");
    await expect
      .poll(() =>
        image.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0),
      )
      .toBe(true);

    await workerContext.setOffline(true);
    await workerPage.getByLabel("Scope / 范围判断").selectOption("annotatable");
    await workerPage
      .getByLabel("Geometry attempt / 几何完成度")
      .selectOption("best_effort_complete");
    await workerPage.getByLabel("非常简单").check();
    await workerPage.getByRole("button", { name: "添加角点对" }).click();
    const canvas = workerPage.getByLabel("全景规范化坐标编辑区");
    await canvas.click({ position: { x: 180, y: 80 } });
    await canvas.click({ position: { x: 210, y: 320 } });
    const topU = workerPage.getByLabel("第 1 对顶点水平坐标");
    await topU.fill("0.321");
    await topU.fill("0.654");
    await workerPage.getByRole("button", { name: "撤销" }).click();
    await expect(topU).toHaveValue("0.321");
    await workerPage.getByRole("button", { name: "重做" }).click();
    await expect(topU).toHaveValue("0.654");

    await expect.soft(workerPage.getByText("离线", { exact: true })).toBeVisible();
    await expect.soft(workerPage.getByRole("button", { name: "提交 Revision" })).toBeDisabled();
    await expect.soft
      .poll(async () => JSON.stringify(await indexedDbContents(workerPage)))
      .toContain(assignmentId);
    const persisted = JSON.stringify(await indexedDbContents(workerPage));
    expect.soft(persisted).toContain("annotation_2d_edit");
    expect.soft(persisted).toContain("0.321");
    expect.soft(persisted).toContain("0.654");
  } finally {
    await workerContext.setOffline(false);
    await Promise.all([adminContext.close(), workerContext.close()]);
  }
});
