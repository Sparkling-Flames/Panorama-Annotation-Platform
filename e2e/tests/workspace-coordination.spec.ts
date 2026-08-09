import process from "node:process";

import { expect, test, type Page } from "@playwright/test";

const WORKER_PASSWORD = process.env.PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD;

if (WORKER_PASSWORD === undefined) {
  throw new Error("Missing PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD");
}

async function loginWorker(page: Page, username: string): Promise<void> {
  await page.goto("/");
  const loginStatus = await page.evaluate(
    async ({ password, workerUsername }) => {
      await fetch("/api/auth/csrf", { credentials: "same-origin" });
      const csrfToken = document.cookie
        .split("; ")
        .find((cookie) => cookie.startsWith("csrftoken="))
        ?.slice("csrftoken=".length);
      const response = await fetch("/api/auth/login", {
        body: JSON.stringify({ password, username: workerUsername }),
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken ?? "" },
        method: "POST",
      });
      return response.status;
    },
    { password: WORKER_PASSWORD, workerUsername: username },
  );
  expect(loginStatus).toBe(200);
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
    await expect(oldPage.getByText("无法取得或续租工作区，当前页面不可编辑。")).toBeVisible();
    await expect(oldPage.getByText("工作区可编辑。")).not.toBeVisible();
  } finally {
    await Promise.all([oldContext.close(), newContext.close()]);
  }
});

test("PAP-IAM-REQ-004 exits editable on network loss and recovers only after retry", async ({
  context,
  page,
}) => {
  await page.clock.install();
  await loginWorker(page, "e2e-network-worker");
  await expect(page.getByText("工作区可编辑。")).toBeVisible();

  await context.setOffline(true);
  await page.clock.fastForward(45_000);
  await expect(page.getByText("网络中断，工作区不可编辑。")).toBeVisible();
  await expect(page.getByText("工作区可编辑。")).not.toBeVisible();

  await context.setOffline(false);
  await page.getByRole("button", { name: "恢复后重试" }).click();
  await expect(page.getByText("工作区可编辑。")).toBeVisible();
});
