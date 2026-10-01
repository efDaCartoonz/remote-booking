import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

describe("FE-03 client Frame and standalone Cancellation browser contract", () => {
  let server;
  let browser;
  let baseUrl;

  before(async () => {
    server = await createServer({ root: frontendRoot, server: { host: "127.0.0.1", port: 0 }, logLevel: "silent" });
    await server.listen();
    baseUrl = `http://127.0.0.1:${server.httpServer.address().port}`;
    browser = await chromium.launch({ headless: true });
  });

  after(async () => {
    if (browser) await browser.close();
    if (server) await server.close();
  });

  it("Frame accepts trusted context, supports UI-017 fields, and allows requesting cancellation link via ticket", async () => {
    const page = await browser.newPage({ timezoneId: "Asia/Yekaterinburg" });
    let created = false;
    let createBody;
    let cancellationLinkRequested = false;

    await page.route("**/api/v1/frame/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());

      if (url.pathname.endsWith("/sessions") && request.method() === "POST") {
        assert.deepEqual(JSON.parse(request.postData()), { case_id: "2000", omnidesk_ticket_number: "123-456789" });
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            token: "frame-test-token",
            omnidesk_ticket_number: "123-456789",
            permissions: ["cards:read", "cards:create"],
            expires_at: new Date(Date.now() + 900_000).toISOString(),
          }),
        });
      }

      if (url.pathname.endsWith("/cards") && request.method() === "GET") {
        assert.equal(request.headers()["x-rdm-frame-token"], "frame-test-token");
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            omnidesk_ticket_number: "123-456789",
            can_create: !created,
            cards: created
              ? [
                  {
                    id: "00000000-0000-0000-0000-000000000001",
                    status: "assigned",
                    status_label: "Назначено",
                    planned_start_at: createBody.planned_start_at,
                    planned_end_at: new Date(Date.parse(createBody.planned_start_at) + 3600_000).toISOString(),
                    planned_duration_minutes: 60,
                    description: "Тестовое подключение",
                    available_actions: ["read", "request_cancellation_link"],
                  },
                ]
              : [],
            client_name: "Иван Клиент",
            client_company_name: "ООО Вектор",
            client_contact_value: "client@example.test",
          }),
        });
      }

      if (url.pathname.endsWith("/cards") && request.method() === "POST") {
        createBody = JSON.parse(request.postData());
        created = true;
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            id: "00000000-0000-0000-0000-000000000001",
            status: "assigned",
            status_label: "Назначено",
            planned_start_at: createBody.planned_start_at,
            planned_end_at: new Date(Date.parse(createBody.planned_start_at) + 3600_000).toISOString(),
            planned_duration_minutes: 60,
            available_actions: ["read", "request_cancellation_link"],
          }),
        });
      }

      if (url.pathname.includes("/cancellation-link") && request.method() === "POST") {
        assert.equal(request.headers()["x-rdm-frame-token"], "frame-test-token");
        assert.equal(request.postData(), null);
        cancellationLinkRequested = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            status: "link_queued",
            expires_in_seconds: 300,
          }),
        });
      }

      return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
    });

    await page.goto(`${baseUrl}/frame`);
    await page.evaluate(() =>
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: "https://attacker.invalid",
          source: window,
          data: { type: "RDM_FRAME_INIT", payload: { case_id: "2000", omnidesk_ticket_number: "123-456789" } },
        })
      )
    );
    await page.waitForTimeout(100);
    assert.equal(await page.getByText("Ожидание параметров обращения от системы...").count(), 1);

    await page.evaluate(() =>
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: "https://iridi.omnidesk.ru",
          source: window,
          data: { type: "RDM_FRAME_INIT", payload: { case_id: "2000", omnidesk_ticket_number: "123-456789" } },
        })
      )
    );
    await page.getByText("Запись на дистанционное подключение").waitFor();

    // Verify case_id is not leaked in DOM
    assert.equal(await page.locator("body").innerText().then((value) => value.includes("2000")), false);

    // Verify UI-017 prefilled client name and company name
    const clientNameInput = page.locator("input[placeholder='Имя клиента']");
    const companyNameInput = page.locator("input[placeholder='Название компании']");
    assert.equal(await clientNameInput.inputValue(), "Иван Клиент");
    assert.equal(await companyNameInput.inputValue(), "ООО Вектор");

    // Edit UI-017 fields
    await clientNameInput.fill("Иван Обновленный");
    await companyNameInput.fill("ООО Новая Компания");

    const start = new Date(Math.ceil((Date.now() + 4 * 3600_000) / 60_000) * 60_000);
    const local = new Intl.DateTimeFormat("sv-SE", {
      timeZone: "Europe/Moscow",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(start);

    await page.locator("#tz-select").fill("Europe/Moscow");
    await page.locator("#tz-select").dispatchEvent("change");
    await page.locator("input[type=date]").fill(local.slice(0, 10));
    await page.locator("input[type=time]").fill(local.slice(11, 16));
    await page.locator("textarea").fill("Тестовое подключение");
    await page.getByRole("button", { name: "Записаться на сеанс" }).click();

    await page.getByText("Назначено").waitFor();
    assert.equal(createBody.planned_start_at, start.toISOString());
    assert.equal(createBody.client_timezone_at_creation, "Europe/Moscow");
    assert.equal(createBody.timezone_source_code, 1);
    assert.equal(createBody.client_name, "Иван Обновленный");
    assert.equal(createBody.client_company_name, "ООО Новая Компания");

    // Verify NO direct cancel or reschedule buttons exist in DOM
    const allButtons = await page.locator("button").allInnerTexts();
    for (const text of allButtons) {
      assert.equal(text.toLowerCase().includes("перенести"), false);
      assert.notEqual(text.trim().toLowerCase(), "отменить");
    }

    // Verify request cancellation link button exists and works
    const requestLinkBtn = page.getByRole("button", { name: "Запросить ссылку для отмены" });
    assert.equal(await requestLinkBtn.count(), 1);
    await requestLinkBtn.click();

    await page.getByText(/Запрос принят\. Ссылка появится в переписке по обращению/).waitFor();
    assert.equal(cancellationLinkRequested, true);

    await page.close();
  });

  it("Standalone /cancel screen strips token from URL, verifies, and performs deliberate confirmation", async () => {
    const page = await browser.newPage();
    const opaqueToken = "safe-opaque-cancel-token-456";
    let verifyDispatched = false;
    let confirmDispatched = false;

    await page.route("**/api/v1/cancellation/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());

      if (url.pathname.endsWith("/verify") && request.method() === "POST") {
        const body = JSON.parse(request.postData());
        assert.equal(body.token, opaqueToken);
        verifyDispatched = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            valid: true,
            status: "active",
            card_public_id: "00000000-0000-0000-0000-000000000001",
            omnidesk_ticket_number: "123-456789",
            planned_start_at: "2026-10-15T09:00:00Z",
            planned_duration_minutes: 60,
            card_status: "assigned",
            card_status_label: "Назначено",
            expires_at: "2026-10-15T09:05:00Z",
            can_cancel: true,
          }),
        });
      }

      if (url.pathname.endsWith("/confirm") && request.method() === "POST") {
        const body = JSON.parse(request.postData());
        assert.equal(body.token, opaqueToken);
        confirmDispatched = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            status: "cancelled",
            card_public_id: "00000000-0000-0000-0000-000000000001",
            cancelled_at: new Date().toISOString(),
            idempotent: false,
          }),
        });
      }

      return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
    });

    // Navigate with token in fragment
    await page.goto(`${baseUrl}/cancel#token=${opaqueToken}`);

    // Verify token was stripped from address bar immediately
    await page.waitForFunction(() => window.location.hash === "");
    const currentUrl = page.url();
    assert.equal(currentUrl.includes(opaqueToken), false, "Token must not remain in browser URL");

    // Verify card summary is displayed
    await page.getByText("Обращение #123-456789").waitFor();
    await page.getByText("Назначено").waitFor();
    assert.equal(verifyDispatched, true);

    // Verify token is NOT rendered in DOM text
    const pageText = await page.locator("body").innerText();
    assert.equal(pageText.includes(opaqueToken), false, "Token must not be visible in DOM text");

    // Perform deliberate confirmation
    const confirmBtn = page.getByRole("button", { name: "Подтвердить отмену записи" });
    assert.equal(await confirmBtn.count(), 1);
    await confirmBtn.click();

    // Verify success banner appears
    await page.getByText("Запись успешно отменена").waitFor();
    assert.equal(confirmDispatched, true);

    await page.close();
  });
});
