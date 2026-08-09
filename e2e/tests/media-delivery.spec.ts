import process from "node:process";

import { expect, test, type Page } from "@playwright/test";

const ADMIN_PASSWORD = process.env.PANORAMA_E2E_ADMIN_PASSWORD;
const COS_ORIGIN = process.env.PANORAMA_E2E_COS_ORIGIN;
const WORKER_PASSWORD = process.env.PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD;
const WORKER_ID = "00000000-0000-4000-8000-000000000008";

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
  expect((await postJson(page, "/api/auth/login", { password, username })).status).toBe(200);
}

test("PAP-MID-SC-008 PAP-MID-SC-010 delivers exact-version media from COS", async ({ browser }) => {
  if (ADMIN_PASSWORD === undefined || WORKER_PASSWORD === undefined || COS_ORIGIN === undefined) {
    throw new Error("Missing media delivery E2E configuration");
  }
  const adminContext = await browser.newContext();
  const workerContext = await browser.newContext();
  try {
    const adminPage = await adminContext.newPage();
    await login(adminPage, "e2e-admin", ADMIN_PASSWORD);
    const preview = await postJson(adminPage, "/api/admin/media/imports/preview", {
      asset_source_key: "panoramas/e2e/media-delivery",
      compressed_source_key: "incoming/e2e/roles/compressed.jpg",
      create_annotation_round: true,
      high_resolution_source_key: "incoming/e2e/roles/high.png",
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
      name: "E2E media delivery batch",
    });
    expect(batch.status).toBe(201);
    const assignment = await postJson(
      adminPage,
      `/api/admin/work-batches/${(batch.body as { batch_id: string }).batch_id}/assignments`,
      { task_id: taskId, worker_id: WORKER_ID },
    );
    expect(assignment.status).toBe(201);
    const assignmentId = (assignment.body as { assignment_id: string }).assignment_id;

    const workerPage = await workerContext.newPage();
    const proxiedImageResponses: string[] = [];
    workerPage.on("response", (response) => {
      if (
        response.url().includes("/api/") &&
        response.headers()["content-type"]?.startsWith("image/")
      ) {
        proxiedImageResponses.push(response.url());
      }
    });
    await login(workerPage, "e2e-media-delivery-worker", WORKER_PASSWORD);
    await workerPage.reload();
    await expect(workerPage.getByText("工作区可编辑。")).toBeVisible();

    const mediaApi = workerPage.waitForResponse((response) =>
      response.url().endsWith(`/api/worker/assignments/${assignmentId}/media`),
    );
    const compressedCos = workerPage.waitForResponse((response) =>
      response.url().startsWith(`${COS_ORIGIN}/incoming/e2e/roles/compressed.jpg`),
    );
    const highCos = workerPage.waitForResponse((response) =>
      response.url().startsWith(`${COS_ORIGIN}/incoming/e2e/roles/high.png`),
    );
    await workerPage.getByRole("button", { name: `打开 ${taskId}` }).click();

    const mediaResponse = await mediaApi;
    expect(mediaResponse.headers()["content-type"]).toContain("application/json");
    const mediaPayload = await mediaResponse.json();
    expect(JSON.stringify(mediaPayload)).not.toContain("data:image");
    expect((await compressedCos).url()).toContain(
      "versionId=e2e-incoming-e2e-roles-compressed.jpg-v1",
    );
    expect((await highCos).url()).toContain("versionId=e2e-incoming-e2e-roles-high.png-v1");
    expect(proxiedImageResponses).toEqual([]);

    const coordinateSpace = workerPage.getByTestId("media-coordinate-space");
    await expect(coordinateSpace).toHaveAttribute("data-coordinate-mapping", "normalized_identity");
    await expect(workerPage.getByRole("button", { name: "切换到高清图" })).toBeVisible();
    await workerPage.getByRole("button", { name: "切换到高清图" }).click();
    await expect(workerPage.getByText("当前媒体：高清图")).toBeVisible();
    await expect(coordinateSpace).toHaveAttribute("data-coordinate-mapping", "normalized_identity");
  } finally {
    await Promise.all([adminContext.close(), workerContext.close()]);
  }
});
