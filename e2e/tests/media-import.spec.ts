import process from "node:process";

import { expect, test } from "@playwright/test";

const ADMIN_PASSWORD = process.env.PANORAMA_E2E_ADMIN_PASSWORD;

test("PAP-MID-SC-005 previews and publishes registered COS versions through Django", async ({
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
  await page.getByRole("button", { name: "预览配对" }).click();

  const preview = page.getByRole("region", { name: "媒体配对预览" });
  await expect(preview).toContainText(`SHA-256: ${"a".repeat(64)}`);
  await expect(preview).toContainText("尺寸: 4096 × 2048");
  await expect(preview).toContainText("坐标映射: normalized_identity");
  await page.getByRole("button", { name: "发布媒体" }).click();

  const publication = page.getByRole("region", { name: "媒体发布结果" });
  await expect(publication).toContainText(/Asset ID: [0-9a-f-]{36}/);
  await expect(publication.getByText(/MediaVariant ID:/)).toHaveCount(2);
});
