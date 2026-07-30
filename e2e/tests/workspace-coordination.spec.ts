import process from "node:process";

import { expect, test } from "@playwright/test";

const WORKER_PASSWORD = process.env.PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD;

test("wires Web Locks into the real worker workspace gate", async ({ context, page }) => {
  if (WORKER_PASSWORD === undefined) {
    throw new Error("Missing PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD");
  }
  await page.goto("/");
  const loginStatus = await page.evaluate(async (password) => {
    await fetch("/api/auth/csrf", { credentials: "same-origin" });
    const csrfToken = document.cookie
      .split("; ")
      .find((cookie) => cookie.startsWith("csrftoken="))
      ?.slice("csrftoken=".length);
    const response = await fetch("/api/auth/login", {
      body: JSON.stringify({ password, username: "e2e-workspace-worker" }),
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken ?? "" },
      method: "POST",
    });
    return response.status;
  }, WORKER_PASSWORD);
  expect(loginStatus).toBe(200);
  await page.reload();
  await expect(page.getByText("工作区可编辑。")).toBeVisible();

  const secondTab = await context.newPage();
  await secondTab.goto("/");
  await expect(secondTab.getByText("此浏览器已有另一个可编辑标签页。")).toBeVisible();
  await expect(secondTab.getByText("工作区可编辑。")).not.toBeVisible();
});
