import { expect, type Page } from "@playwright/test";

export async function postJson(page: Page, endpoint: string, body: object) {
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

export async function loginViaApi(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/");
  await page.evaluate(async () => {
    await fetch("/api/auth/csrf", { credentials: "same-origin" });
  });
  expect((await postJson(page, "/api/auth/login", { password, username })).status).toBe(200);

  const notice = await page.evaluate(async () => {
    const response = await fetch("/api/privacy/notice", { credentials: "same-origin" });
    return { body: await response.json(), status: response.status };
  });
  expect([200, 403]).toContain(notice.status);
  if (notice.status === 200) {
    expect(notice.body).toMatchObject({ notice_version: expect.any(String) });
    const version = (notice.body as { notice_version: string }).notice_version;
    expect([200, 201]).toContain(
      (await postJson(page, "/api/privacy/notice/accept", { notice_version: version })).status,
    );
  }
}
