import process from "node:process";

import { expect, test, type Page } from "@playwright/test";

const ADMIN_PASSWORD = process.env.PANORAMA_E2E_ADMIN_PASSWORD;

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
