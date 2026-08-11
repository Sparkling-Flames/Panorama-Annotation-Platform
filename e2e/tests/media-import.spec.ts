import { spawnSync } from "node:child_process";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import { expect, test, type Locator, type Page } from "@playwright/test";

import { loginViaApi, postJson } from "./api-helpers";

const ADMIN_PASSWORD = process.env.PANORAMA_E2E_ADMIN_PASSWORD;
const HIGH_RESOLUTION_HASH = "1f395f818a59308471cd4998d65be7f2d8ea81ea76d2bb521194e0f2d5498b8e";
const COMPRESSED_HASH = "2536b2aa8248cfec5b5165ccbd69bcbb0510b74547c13232926772a41fc2b26c";

type PreviewPayload = {
  plan_sha256: string;
  preview_id: string;
};

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined) {
    throw new Error(`Missing required E2E environment variable: ${name}`);
  }
  return value;
}

async function loginAdministrator(page: Page): Promise<void> {
  if (ADMIN_PASSWORD === undefined) {
    throw new Error("Missing PANORAMA_E2E_ADMIN_PASSWORD");
  }
  await loginViaApi(page, "e2e-admin", ADMIN_PASSWORD);
  await page.reload();
  await expect(page.getByRole("heading", { name: "管理员 COS 媒体导入" })).toBeVisible();
}

async function createPreview(
  page: Page,
  {
    assetSourceKey,
    prefix,
    createAnnotationRound = false,
  }: { assetSourceKey: string; prefix: string; createAnnotationRound?: boolean },
): Promise<PreviewPayload> {
  await page.getByLabel("Asset source key").fill(assetSourceKey);
  await page.getByLabel("COS 前缀").fill(prefix);
  await page.getByRole("button", { name: "浏览 COS 候选" }).click();
  await page.getByLabel("高清候选").selectOption(`${prefix}/high.png`);
  await page.getByLabel("压缩候选").selectOption(`${prefix}/compressed.jpg`);
  if (createAnnotationRound) {
    await page.getByLabel("请求新轮次").check();
  }
  const previewResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/admin/media/imports/preview"),
  );
  await page.getByRole("button", { name: "预览配对" }).click();
  const response = await previewResponse;
  expect(response.status()).toBe(200);
  return (await response.json()) as PreviewPayload;
}

function expirePreview(previewId: string): void {
  const repositoryRoot = requiredEnvironment("PANORAMA_E2E_REPOSITORY_ROOT");
  const result = spawnSync(
    requiredEnvironment("PANORAMA_E2E_PYTHON"),
    [
      path.join(repositoryRoot, "backend", "manage.py"),
      "shell",
      "-c",
      [
        "from datetime import timedelta",
        "from django.utils import timezone",
        "from media.models import MediaImportPreview",
        `updated = MediaImportPreview.objects.filter(preview_id='${previewId}').update(expires_at=timezone.now() - timedelta(seconds=1))`,
        "assert updated == 1",
      ].join("; "),
    ],
    {
      cwd: repositoryRoot,
      encoding: "utf8",
      env: {
        ...process.env,
        DJANGO_SETTINGS_MODULE: "panorama_annotation.test_settings",
        PANORAMA_TEST_SQLITE_PATH: requiredEnvironment("PANORAMA_E2E_SQLITE_PATH"),
      },
    },
  );
  expect(result.status, result.stderr).toBe(0);
}

async function setCosDrift(sourceKey: string | null): Promise<void> {
  await writeFile(
    requiredEnvironment("PANORAMA_E2E_COS_CONTROL_FILE"),
    JSON.stringify({ drift_source_key: sourceKey }),
    "utf8",
  );
}

async function imageIdentity(image: Locator) {
  return image.evaluate(async (node) => {
    const element = node as HTMLImageElement;
    await element.decode();
    const bytes = new Uint8Array(await (await fetch(element.src)).arrayBuffer());
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return {
      contentLength: bytes.length,
      contentSha256: [...new Uint8Array(digest)]
        .map((value) => value.toString(16).padStart(2, "0"))
        .join(""),
      height: element.naturalHeight,
      width: element.naturalWidth,
    };
  });
}

test("PAP-MID-SC-003 PAP-MID-SC-005 PAP-MID-SC-006 imports real media and creates explicit rounds", async ({
  page,
}) => {
  await loginAdministrator(page);
  await page.getByLabel("Asset source key").fill("panoramas/e2e");
  await page.getByLabel("COS 前缀").fill("incoming/e2e");
  await page.getByRole("button", { name: "浏览 COS 候选" }).click();
  await page.getByLabel("高清候选").selectOption("incoming/e2e/high.png");
  await page.getByLabel("压缩候选").selectOption("incoming/e2e/compressed.jpg");
  await page.getByLabel("请求新轮次").check();
  await page.getByRole("button", { name: "预览配对" }).click();

  const preview = page.getByRole("region", { name: "媒体配对预览" });
  await expect(preview).toContainText(`SHA-256: ${HIGH_RESOLUTION_HASH}`);
  await expect(preview).toContainText("尺寸: 32 × 16");
  await expect(preview).toContainText("坐标映射: normalized_identity");
  expect(await imageIdentity(page.getByRole("img", { name: "高清候选预览" }))).toEqual({
    contentLength: 109,
    contentSha256: HIGH_RESOLUTION_HASH,
    height: 16,
    width: 32,
  });
  expect(await imageIdentity(page.getByRole("img", { name: "压缩候选预览" }))).toEqual({
    contentLength: 214,
    contentSha256: COMPRESSED_HASH,
    height: 8,
    width: 16,
  });
  await page.getByRole("button", { name: "发布媒体并创建 Task" }).click();

  const publication = page.getByRole("region", { name: "媒体发布结果" });
  await expect(publication).toContainText(/Asset ID: [0-9a-f-]{36}/);
  await expect(publication.getByText(/已创建 .* MediaVariant ID:/)).toHaveCount(2);
  const assetId = (await publication.textContent())?.match(/Asset ID:\s*([0-9a-f-]{36})/)?.[1];
  const firstTaskId = (await publication.textContent())?.match(/Task ID:\s*([0-9a-f-]{36})/)?.[1];
  expect(assetId).toBeDefined();
  expect(firstTaskId).toBeDefined();
  await expect(publication).toContainText("这是该 Asset 的首轮 Task，无上一轮。");

  await page.getByRole("button", { name: "预览配对" }).click();
  await expect(preview).toContainText("将复用既有 Asset。");
  await page.getByRole("button", { name: "发布媒体并创建 Task" }).click();

  await expect(publication).toContainText("已复用 Asset。");
  await expect(publication).toContainText(`Asset ID: ${assetId}`);
  await expect(publication.getByText(/已复用 .* MediaVariant ID:/)).toHaveCount(2);
  await expect(publication).toContainText(`上一轮 Task ID: ${firstTaskId}`);
  const secondTaskId = (await publication.textContent())?.match(/Task ID:\s*([0-9a-f-]{36})/)?.[1];
  expect(secondTaskId).toBeDefined();
  expect(secondTaskId).not.toBe(firstTaskId);
});

test("PAP-MID-REQ-003 paginates registered candidates and excludes an unregistered COS object", async ({
  page,
}) => {
  await loginAdministrator(page);
  await page.getByLabel("COS 前缀").fill("incoming/e2e/page");
  await page.getByRole("button", { name: "浏览 COS 候选" }).click();
  await expect(page.getByLabel("高清候选").locator("option")).toHaveCount(101);
  await page.getByRole("button", { name: "加载更多" }).click();
  await expect(page.getByLabel("高清候选").locator("option")).toHaveCount(102);
  await expect(page.getByRole("button", { name: "加载更多" })).not.toBeVisible();

  await page.getByLabel("COS 前缀").fill("incoming/e2e/unregistered");
  await page.getByRole("button", { name: "浏览 COS 候选" }).click();
  await expect(page.getByLabel("高清候选")).not.toBeVisible();
});

test("PAP-MID-SC-001 PAP-MID-SC-003 enforces mutually exclusive roles and previews real bytes", async ({
  page,
}) => {
  await loginAdministrator(page);
  await page.getByLabel("Asset source key").fill("panoramas/e2e-role-preview");
  await page.getByLabel("COS 前缀").fill("incoming/e2e/roles");
  await page.getByRole("button", { name: "浏览 COS 候选" }).click();

  const highResolution = page.getByLabel("高清候选");
  const compressed = page.getByLabel("压缩候选");
  await highResolution.selectOption("incoming/e2e/roles/high.png");
  expect(
    await compressed
      .locator('option[value="incoming/e2e/roles/high.png"]')
      .evaluate((option: HTMLOptionElement) => option.disabled),
  ).toBe(true);
  await compressed.selectOption("incoming/e2e/roles/compressed.jpg");
  expect(
    await highResolution
      .locator('option[value="incoming/e2e/roles/compressed.jpg"]')
      .evaluate((option: HTMLOptionElement) => option.disabled),
  ).toBe(true);
  await page.getByRole("button", { name: "预览配对" }).click();

  expect(await imageIdentity(page.getByRole("img", { name: "高清候选预览" }))).toEqual({
    contentLength: 109,
    contentSha256: HIGH_RESOLUTION_HASH,
    height: 16,
    width: 32,
  });
  expect(await imageIdentity(page.getByRole("img", { name: "压缩候选预览" }))).toEqual({
    contentLength: 214,
    contentSha256: COMPRESSED_HASH,
    height: 8,
    width: 16,
  });
  expect(
    await page.evaluate(() => ({
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    })),
  ).toEqual({ localStorage: {}, sessionStorage: {} });
});

test("PAP-MID-SC-007 cancels a preview and rejects a later publish of that preview", async ({
  page,
}) => {
  await loginAdministrator(page);
  const preview = await createPreview(page, {
    assetSourceKey: "panoramas/e2e-cancel",
    prefix: "incoming/e2e/cancel",
  });
  await page.getByRole("button", { name: "取消预览" }).click();
  await expect(page.getByRole("region", { name: "媒体配对预览" })).not.toBeVisible();

  await expect(
    postJson(page, "/api/admin/media/imports/publish", {
      expected_plan_sha256: preview.plan_sha256,
      preview_id: preview.preview_id,
    }),
  ).resolves.toEqual({
    body: { error: { code: "media_import_preview_cancelled" } },
    status: 409,
  });
});

test("PAP-MID-REQ-003 rejects an expired preview through the real publish endpoint", async ({
  page,
}) => {
  await loginAdministrator(page);
  const preview = await createPreview(page, {
    assetSourceKey: "panoramas/e2e-expire",
    prefix: "incoming/e2e/expire",
  });
  expirePreview(preview.preview_id);

  await page.getByRole("button", { name: "发布媒体" }).click();
  await expect(page.getByRole("alert")).toHaveText("该预览已过期，请重新预览。");
  await expect(page.getByRole("region", { name: "媒体发布结果" })).not.toBeVisible();
});

test("PAP-MID-SC-005 retry returns the same publication response", async ({ page }) => {
  await loginAdministrator(page);
  const preview = await createPreview(page, {
    assetSourceKey: "panoramas/e2e-repeat",
    createAnnotationRound: true,
    prefix: "incoming/e2e/repeat",
  });
  const firstPublishResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/admin/media/imports/publish"),
  );
  await page.getByRole("button", { name: "发布媒体并创建 Task" }).click();
  const firstResponse = await firstPublishResponse;
  const firstBody = await firstResponse.json();

  const replay = await postJson(page, "/api/admin/media/imports/publish", {
    expected_plan_sha256: preview.plan_sha256,
    preview_id: preview.preview_id,
  });
  expect(replay.status).toBe(firstResponse.status());
  expect(replay.body).toEqual(firstBody);
});

test("PAP-MID-SC-002 rejects publish when COS metadata changes after preview", async ({ page }) => {
  await setCosDrift(null);
  try {
    await loginAdministrator(page);
    await createPreview(page, {
      assetSourceKey: "panoramas/e2e-drift",
      prefix: "incoming/e2e/drift",
    });
    await setCosDrift("incoming/e2e/drift/high.png");

    await page.getByRole("button", { name: "发布媒体" }).click();
    await expect(page.getByRole("alert")).toHaveText("COS 对象完整性校验失败，请重新登记该版本。");
    await expect(page.getByRole("region", { name: "媒体发布结果" })).not.toBeVisible();
  } finally {
    await setCosDrift(null);
  }
});
