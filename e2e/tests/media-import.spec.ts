import process from "node:process";

import { expect, test, type Locator } from "@playwright/test";

const ADMIN_PASSWORD = process.env.PANORAMA_E2E_ADMIN_PASSWORD;
const HIGH_RESOLUTION_HASH = "1f395f818a59308471cd4998d65be7f2d8ea81ea76d2bb521194e0f2d5498b8e";
const COMPRESSED_HASH = "2536b2aa8248cfec5b5165ccbd69bcbb0510b74547c13232926772a41fc2b26c";

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
  if (ADMIN_PASSWORD === undefined) {
    throw new Error("Missing PANORAMA_E2E_ADMIN_PASSWORD");
  }
  await page.goto("/");
  const loginStatus = await page.evaluate(async (password) => {
    await fetch("/api/auth/csrf", { credentials: "same-origin" });
    const csrfToken = document.cookie
      .split("; ")
      .find((cookie) => cookie.startsWith("csrftoken="))
      ?.slice("csrftoken=".length);
    const response = await fetch("/api/auth/login", {
      body: JSON.stringify({ password, username: "e2e-admin" }),
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken ?? "" },
      method: "POST",
    });
    return response.status;
  }, ADMIN_PASSWORD);
  expect(loginStatus).toBe(200);
  await page.reload();

  await expect(page.getByRole("heading", { name: "管理员 COS 媒体导入" })).toBeVisible();
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
