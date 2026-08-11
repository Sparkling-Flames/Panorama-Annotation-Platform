import process from "node:process";

import { expect, test, type Page } from "@playwright/test";

import { loginViaApi } from "./api-helpers";

const WORKER_PASSWORD = process.env.PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD;

if (WORKER_PASSWORD === undefined) {
  throw new Error("Missing PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD");
}
const VERIFIED_WORKER_PASSWORD = WORKER_PASSWORD;

async function loginWorker(page: Page, username: string): Promise<void> {
  await loginViaApi(page, username, VERIFIED_WORKER_PASSWORD);
  await page.reload();
}

test("PAP-IAM-SC-008 first tab becomes editable before a second browser tab is blocked", async ({
  context,
  page,
}) => {
  await loginWorker(page, "e2e-workspace-worker");

  await expect(page.getByText("工作区可编辑。")).toBeVisible();

  const secondTab = await context.newPage();
  await secondTab.goto("/");
  await expect(secondTab.getByText("此浏览器已有另一个可编辑标签页。")).toBeVisible();
  await expect(secondTab.getByText("工作区可编辑。")).not.toBeVisible();
});

test("PAP-IAM-SC-007 requires takeover and makes the old page fail its next renewal", async ({
  browser,
}) => {
  const oldContext = await browser.newContext();
  const newContext = await browser.newContext();

  try {
    const oldPage = await oldContext.newPage();
    await oldPage.clock.install();
    await loginWorker(oldPage, "e2e-takeover-worker");
    await expect(oldPage.getByText("工作区可编辑。")).toBeVisible();

    const newPage = await newContext.newPage();
    await loginWorker(newPage, "e2e-takeover-worker");
    const takeoverRequired = newPage.getByText("另一设备持有工作区租约，需要明确接管。");
    await expect(takeoverRequired).toBeVisible();
    await expect(newPage.getByText("工作区可编辑。")).not.toBeVisible();
    await newPage.getByRole("button", { name: "接管工作区" }).click();
    await expect(newPage.getByText("工作区可编辑。")).toBeVisible();

    const renewal = oldPage.waitForResponse((response) =>
      response.url().endsWith("/api/workspace/renew"),
    );
    await oldPage.clock.fastForward(45_000);
    expect((await renewal).status()).toBe(409);
    await expect(oldPage.getByText("工作区已失效，已保留本地恢复副本。")).toBeVisible();
    await expect(oldPage.getByText("工作区可编辑。")).not.toBeVisible();
  } finally {
    await Promise.all([oldContext.close(), newContext.close()]);
  }
});

test("PAP-IAM-REQ-004 PAP-AOF-SC-007 enters offline mode and renews after connectivity returns", async ({
  context,
  page,
}) => {
  await page.clock.install();
  await loginWorker(page, "e2e-network-worker");
  await expect(page.getByText("工作区可编辑。")).toBeVisible();

  await context.setOffline(true);
  await page.clock.fastForward(45_000);
  await expect(page.getByText("离线", { exact: true })).toBeVisible();
  await expect(page.getByText("工作区可编辑。")).not.toBeVisible();

  const renewal = page.waitForResponse((response) =>
    response.url().endsWith("/api/workspace/renew"),
  );
  await context.setOffline(false);
  expect((await renewal).status()).toBe(204);
  await expect(page.getByText("工作区可编辑。")).toBeVisible();
});
