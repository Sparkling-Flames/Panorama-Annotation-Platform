import { expect, test } from "@playwright/test";

test("starts the empty browser application shell", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Panorama Annotation Platform" })).toBeVisible();
  await expect(page.getByText("OpenSpec 实施中")).toBeVisible();
});
