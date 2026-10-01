import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const frontendRoot = resolve(__dirname, "..");

function mockUser(id, roleIds = [4], overrides = {}) {
  const roleMap = {
    1: "Специалист L1",
    2: "Инженер L2",
    3: "Руководитель",
    4: "Администратор",
  };
  return {
    id,
    username: `user_${id}`,
    full_name: `User ${id} Full Name`,
    roles: roleIds.map((r) => ({ id: r, name: roleMap[r] || `Role ${r}` })),
    timezone: "Asia/Yekaterinburg",
    email: `user_${id}@example.test`,
    phone: "+79991234567",
    omnidesk_staff_id: null,
    is_active: true,
    telegram_chat_id: null,
    bitrix24_user_id: null,
    ...overrides,
  };
}

describe("FE-04 Admin and Reports Browser E2E Suite", () => {
  let viteServer;
  let browser;
  let baseUrl;

  before(async () => {
    viteServer = await createServer({
      root: frontendRoot,
      server: { host: "127.0.0.1", port: 0 },
      logLevel: "silent",
    });
    await viteServer.listen();
    const port = viteServer.httpServer.address().port;
    baseUrl = `http://127.0.0.1:${port}`;
    browser = await chromium.launch({ headless: true });
  });

  after(async () => {
    if (browser) await browser.close();
    if (viteServer) await viteServer.close();
  });

  // -------------------------------------------------------------------------
  // Scenario 1: ADMIN Workspace, User Creation, Password Sanitization & Deactivation
  // -------------------------------------------------------------------------
  describe("Scenario 1: ADMIN Workspace, User Creation, Password Sanitization & Deactivation", () => {
    it("renders tabs, sends password in create request, clears password from DOM/response, and confirms deactivation", async () => {
      const page = await browser.newPage({ timezoneId: "Asia/Yekaterinburg" });
      const interceptedRequests = [];
      let deactivationConfirmed = false;

      const adminUser = mockUser(1, [4], { username: "admin_master", full_name: "Главный Администратор" });
      let existingUsers = [
        adminUser,
        mockUser(2, [1, 2], { username: "engineer_bob", full_name: "Боб Инженеров", is_active: true }),
      ];

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const method = req.method();
        const postData = req.postData();

        interceptedRequests.push({ url, method, body: postData });

        if (url.includes("/auth/me")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(adminUser),
          });
        }

        if (url.endsWith("/admin/users") && method === "GET") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(existingUsers),
          });
        }

        if (url.endsWith("/admin/users") && method === "POST") {
          const payload = JSON.parse(postData);
          const newUser = {
            id: 10,
            username: payload.username,
            full_name: payload.full_name,
            email: payload.email || null,
            phone: payload.phone || null,
            omnidesk_staff_id: payload.omnidesk_staff_id || null,
            is_active: payload.is_active !== undefined ? payload.is_active : true,
            roles: payload.roles.map((r) => ({ id: r, name: `Role ${r}` })),
            timezone: "Asia/Yekaterinburg",
            telegram_chat_id: null,
            bitrix24_user_id: null,
          };
          existingUsers.push(newUser);
          // Backend response must never contain password_hash or plain password
          assert.equal(newUser.password, undefined);
          assert.equal(newUser.password_hash, undefined);
          return route.fulfill({
            status: 201,
            contentType: "application/json",
            body: JSON.stringify(newUser),
          });
        }

        if (url.includes("/admin/users/2") && method === "PATCH") {
          const patchData = JSON.parse(postData);
          if (patchData.is_active === false) {
            deactivationConfirmed = true;
          }
          const updated = { ...existingUsers.find((u) => u.id === 2), ...patchData };
          existingUsers = existingUsers.map((u) => (u.id === 2 ? updated : u));
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(updated),
          });
        }

        if (url.includes("/admin/users/2/roles") && method === "PUT") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(existingUsers.find((u) => u.id === 2)),
          });
        }

        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not_found" }) });
      });

      // 1. Visit /admin as Admin
      await page.goto(`${baseUrl}/admin`);
      await page.waitForSelector('[data-test="tab-users"]');

      // Verify all 3 top-level tabs are visible
      assert.equal(await page.locator('[data-test="tab-users"]').isVisible(), true);
      assert.equal(await page.locator('[data-test="tab-schedules"]').isVisible(), true);
      assert.equal(await page.locator('[data-test="tab-settings"]').isVisible(), true);

      // Verify users table rendered
      await page.waitForSelector('[data-test="table-users"]');
      assert.match(await page.textContent('[data-test="table-users"]'), /admin_master/);
      assert.match(await page.textContent('[data-test="table-users"]'), /engineer_bob/);

      // 2. Open Create User Modal and submit
      await page.click('[data-test="btn-open-create-user"]');
      await page.waitForSelector('[data-test="modal-create-user"]');

      const secretPassword = "SuperSecretPassword123!";
      await page.fill('[data-test="input-new-username"]', "alice_admin");
      await page.fill('[data-test="input-new-password"]', secretPassword);
      await page.fill('[data-test="input-new-fullname"]', "Алиса Администратор");
      await page.fill('[data-test="input-new-email"]', "alice@example.test");
      await page.click('[data-test="btn-submit-create-user"]');

      // Wait for success alert
      await page.waitForSelector('[data-test="admin-success"]');
      assert.match(await page.textContent('[data-test="admin-success"]'), /Пользователь alice_admin успешно создан/);

      // Verify POST /admin/users request contained the plain password
      const createCall = interceptedRequests.find((r) => r.url.endsWith("/admin/users") && r.method === "POST");
      assert.ok(createCall, "Create user POST request must be dispatched");
      const createPayload = JSON.parse(createCall.body);
      assert.equal(createPayload.username, "alice_admin");
      assert.equal(createPayload.password, secretPassword);

      // Verify secret password is NOT left anywhere in DOM text or form inputs
      const pageText = await page.locator("body").innerText();
      assert.equal(pageText.includes(secretPassword), false, "Password must not leak into visible DOM text");
      const allInputValues = await page.$$eval("input", (inputs) => inputs.map((i) => i.value));
      for (const val of allInputValues) {
        assert.equal(val.includes(secretPassword), false, "Password must be wiped from all inputs");
      }

      // 3. User Deactivation Confirmation Check
      // Step 3a: Start edit on engineer_bob (ID 2), uncheck active, dismiss dialog -> PATCH not dispatched
      await page.click('[data-test="btn-edit-user-2"]');
      await page.waitForSelector('[data-test="modal-edit-user"]');

      // Uncheck active checkbox
      await page.uncheck('[data-test="checkbox-edit-active"]');

      // Set up dialog dismiss
      page.once("dialog", async (dialog) => {
        assert.match(dialog.message(), /Вы действительно хотите деактивировать пользователя engineer_bob/);
        await dialog.dismiss();
      });

      await page.click('[data-test="btn-save-edit-user"]');
      await page.waitForTimeout(100);

      // Verify PATCH was NOT dispatched on dialog cancellation
      assert.equal(deactivationConfirmed, false, "Deactivation must not occur if user dismisses confirmation dialog");

      // Step 3b: Accept dialog -> PATCH is dispatched with is_active: false
      page.once("dialog", async (dialog) => {
        assert.match(dialog.message(), /Вы действительно хотите деактивировать пользователя engineer_bob/);
        await dialog.accept();
      });

      await page.click('[data-test="btn-save-edit-user"]');
      await page.waitForSelector('[data-test="admin-success"]');
      assert.equal(deactivationConfirmed, true, "Deactivation PATCH must be dispatched when user accepts dialog");

      await page.close();
    });
  });

  // -------------------------------------------------------------------------
  // Scenario 2: Distribution Membership Comment Validation
  // -------------------------------------------------------------------------
  describe("Scenario 2: Distribution Membership Comment Validation", () => {
    it("blocks distribution PUT without comment and dispatches PUT with required comment field", async () => {
      const page = await browser.newPage({ timezoneId: "Asia/Yekaterinburg" });
      const interceptedRequests = [];

      const adminUser = mockUser(1, [4]);
      const members = [
        {
          id: 1,
          user_id: 22,
          pool_code: 2,
          is_enabled: true,
          enabled_by_id: 1,
          comment: "Начальное включение",
        },
      ];

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const method = req.method();
        const postData = req.postData();

        interceptedRequests.push({ url, method, body: postData });

        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(adminUser) });
        }
        if (url.endsWith("/admin/users")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify([adminUser, mockUser(22, [2], { username: "l2_vasya", full_name: "Василий L2" })]),
          });
        }
        if (url.includes("/admin/distribution/members") && method === "GET") {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(members) });
        }
        if (url.includes("/admin/distribution/members/22") && method === "PUT") {
          const body = JSON.parse(postData);
          members[0].is_enabled = body.enabled;
          members[0].comment = body.comment;
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(members[0]) });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not_found" }) });
      });

      await page.goto(`${baseUrl}/admin`);
      await page.waitForSelector('[data-test="tab-schedules"]');
      await page.click('[data-test="tab-schedules"]');

      await page.waitForSelector('[data-test="subtab-distribution"]');
      await page.click('[data-test="subtab-distribution"]');

      // Wait for the distribution row to appear
      await page.waitForSelector('[data-test="dist-row-2-22"]');
      assert.match(await page.textContent('[data-test="dist-row-2-22"]'), /Василий L2/);
      assert.match(await page.textContent('[data-test="dist-row-2-22"]'), /Включён/);

      // Open exclusion form
      await page.click('[data-test="btn-toggle-dist-2-22"]');
      await page.waitForSelector('[data-test="panel-edit-distribution"]');

      // Attempt submit with empty comment (client validation should prevent HTTP request)
      await page.fill('[data-test="textarea-dist-comment"]', "");
      await page.click('[data-test="btn-save-distribution"]');

      const distCallsBefore = interceptedRequests.filter(
        (r) => r.url.includes("/admin/distribution/members/22") && r.method === "PUT"
      );
      assert.equal(distCallsBefore.length, 0, "PUT request must NOT be sent without mandatory comment");

      // Now fill required comment and submit
      const justification = "Приказ руководителя №104 от 01.10.2026: перевод на спецпроект";
      await page.fill('[data-test="textarea-dist-comment"]', justification);
      await page.click('[data-test="btn-save-distribution"]');

      // Wait for success
      await page.waitForSelector('[data-test="admin-success"]');
      assert.match(await page.textContent('[data-test="admin-success"]'), /Участие сотрудника в распределении обновлено/);

      const distCallsAfter = interceptedRequests.filter(
        (r) => r.url.includes("/admin/distribution/members/22") && r.method === "PUT"
      );
      assert.equal(distCallsAfter.length, 1, "PUT request must be dispatched exactly once with comment");
      const putBody = JSON.parse(distCallsAfter[0].body);
      assert.equal(putBody.pool_code, 2);
      assert.equal(putBody.enabled, false);
      assert.equal(putBody.comment, justification);

      await page.close();
    });
  });

  // -------------------------------------------------------------------------
  // Scenario 3: L1/L2 Boundary - No Admin/Reports Access & No Leaked Requests
  // -------------------------------------------------------------------------
  describe("Scenario 3: Role Boundaries - L1 and L2 cannot view or trigger /admin and /reports API", () => {
    it("forbids L1 from accessing /admin and /reports without triggering any administrative or reporting requests", async () => {
      const page = await browser.newPage();
      const interceptedRequests = [];

      const l1User = mockUser(11, [1], { username: "l1_operator", full_name: "Оператор L1" });

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        interceptedRequests.push({ url: req.url(), method: req.method() });

        if (req.url().includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(l1User) });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not_found" }) });
      });

      // 1. L1 navigates to /admin
      await page.goto(`${baseUrl}/admin`);
      await page.waitForSelector("body");
      await page.waitForTimeout(100);

      // Verify no admin elements rendered
      assert.equal(await page.locator('[data-test="section-users"]').count(), 0);
      assert.equal(await page.locator(".admin-workspace").count(), 0);
      assert.equal(await page.locator('[data-test="tab-users"]').count(), 0);

      // Verify NO requests to /api/v1/admin/* were sent
      const adminCalls = interceptedRequests.filter((r) => r.url.includes("/api/v1/admin"));
      assert.equal(adminCalls.length, 0, "L1 must not trigger any /api/v1/admin/* requests");

      // 2. L1 navigates to /reports
      await page.goto(`${baseUrl}/reports`);
      await page.waitForSelector("body");
      await page.waitForTimeout(100);

      // Verify no reports elements rendered
      assert.equal(await page.locator('[data-test="reports-workspace"]').count(), 0);
      assert.equal(await page.locator('[data-test="section-summary"]').count(), 0);

      // Verify NO requests to /api/v1/reports/* were sent
      const reportsCalls = interceptedRequests.filter((r) => r.url.includes("/api/v1/reports"));
      assert.equal(reportsCalls.length, 0, "L1 must not trigger any /api/v1/reports/* requests");

      await page.close();
    });

    it("forbids L2 from accessing /admin and /reports without triggering any administrative or reporting requests", async () => {
      const page = await browser.newPage();
      const interceptedRequests = [];

      const l2User = mockUser(22, [2], { username: "l2_specialist", full_name: "Специалист L2" });

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        interceptedRequests.push({ url: req.url(), method: req.method() });

        if (req.url().includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(l2User) });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not_found" }) });
      });

      // 1. L2 navigates to /admin
      await page.goto(`${baseUrl}/admin`);
      await page.waitForSelector("body");
      await page.waitForTimeout(100);

      assert.equal(await page.locator('[data-test="section-users"]').count(), 0);
      assert.equal(await page.locator(".admin-workspace").count(), 0);
      assert.equal(interceptedRequests.filter((r) => r.url.includes("/api/v1/admin")).length, 0);

      // 2. L2 navigates to /reports
      await page.goto(`${baseUrl}/reports`);
      await page.waitForSelector("body");
      await page.waitForTimeout(100);

      assert.equal(await page.locator('[data-test="reports-workspace"]').count(), 0);
      assert.equal(interceptedRequests.filter((r) => r.url.includes("/api/v1/reports")).length, 0);

      await page.close();
    });
  });

  // -------------------------------------------------------------------------
  // Scenario 4: MANAGER on /reports - Period Selection, ISO Dates & Share Formatting
  // -------------------------------------------------------------------------
  describe("Scenario 4: MANAGER on /reports - Period selection, ISO datetime parameters & null-share display", () => {
    it("formats from/to query params as ISO strings with offset/UTC, and displays null shares as 'нет данных' rather than 0%", async () => {
      const page = await browser.newPage({ timezoneId: "Asia/Yekaterinburg" });
      const reportRequests = [];

      const managerUser = mockUser(33, [3], { username: "manager_boss", full_name: "Руководитель Отдела" });

      const mockSummary = {
        created: 42,
        completed: 35,
        rejected_share: { numerator: 0, denominator: 0, value: null }, // Share is null
        repeat_rejected_share: { numerator: 0, denominator: 0, value: null }, // Share is null
        overdue: { count: 3 },
        urgent: { count: 5 },
        urgent_collisions: { count: 0 },
      };

      const mockOverdue = {
        items: [
          {
            public_id: "00000000-0000-0000-0000-000000000001",
            number: "RDM-OVERDUE-1",
            status: "assigned",
            status_label: "Назначено",
            planned_start_at: "2026-10-01T06:00:00Z",
          },
        ],
        total: 1,
        limit: 20,
        offset: 0,
      };

      const mockL2Load = {
        items: [
          {
            user_id: 22,
            full_name: "Василий L2",
            assigned: 10,
            completed: 8,
            planned_minutes: 600,
          },
        ],
      };

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const urlStr = req.url();
        const url = new URL(urlStr);

        if (urlStr.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(managerUser) });
        }

        if (url.pathname.includes("/reports/summary")) {
          reportRequests.push({ path: "summary", from: url.searchParams.get("from"), to: url.searchParams.get("to") });
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockSummary) });
        }

        if (url.pathname.includes("/reports/overdue")) {
          reportRequests.push({ path: "overdue", from: url.searchParams.get("from"), to: url.searchParams.get("to") });
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockOverdue) });
        }

        if (url.pathname.includes("/reports/l2-load")) {
          reportRequests.push({ path: "l2-load", from: url.searchParams.get("from"), to: url.searchParams.get("to") });
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockL2Load) });
        }

        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not_found" }) });
      });

      // 1. Visit /reports as Manager
      await page.goto(`${baseUrl}/reports`);
      await page.waitForSelector('[data-test="reports-workspace"]');

      // Verify period query parameters were ISO formatted with timezone information
      assert.ok(reportRequests.length >= 3, "Initial reports requests must be dispatched");
      const summaryReq = reportRequests.find((r) => r.path === "summary");
      assert.ok(summaryReq.from, "from parameter must be provided");
      assert.ok(summaryReq.to, "to parameter must be provided");

      // Verify from/to ISO format: e.g. "2026-08-31T19:00:00.000Z" or "2026-09-01T00:00:00+05:00"
      const isoRegex = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)$/;
      assert.match(summaryReq.from, isoRegex);
      assert.match(summaryReq.to, isoRegex);

      // 2. Verify null shares are rendered as "нет данных" (NOT "0%" or "null%")
      const rejectedShareText = await page.textContent('[data-test="stat-rejected-share"]');
      const repeatRejectedShareText = await page.textContent('[data-test="stat-repeat-rejected-share"]');

      assert.match(rejectedShareText, /нет данных/);
      assert.equal(rejectedShareText.includes("0%"), false, "Null share must not be formatted as 0%");
      assert.equal(rejectedShareText.includes("0.0%"), false, "Null share must not be formatted as 0.0%");

      assert.match(repeatRejectedShareText, /нет данных/);
      assert.equal(repeatRejectedShareText.includes("0%"), false, "Null share must not be formatted as 0%");

      // 3. Verify overdue and L2 load metrics rendered properly
      assert.match(await page.textContent('[data-test="table-overdue"]'), /RDM-OVERDUE-1/);
      assert.match(await page.textContent('[data-test="table-l2-load"]'), /Василий L2/);
      assert.match(await page.textContent('[data-test="table-l2-load"]'), /10 ч/);

      // 4. Test period preset button (e.g. "Этот месяц")
      reportRequests.length = 0;
      await page.click('[data-test="preset-month"]');
      await page.waitForTimeout(100);

      assert.ok(reportRequests.length >= 3, "Preset click must reload reports with new period");
      const monthReq = reportRequests.find((r) => r.path === "summary");
      assert.match(monthReq.from, isoRegex);
      assert.match(monthReq.to, isoRegex);

      await page.close();
    });
  });

  // -------------------------------------------------------------------------
  // Scenario 5: Privacy - No case_id or secrets in Requests/DOM, Integrations Status
  // -------------------------------------------------------------------------
  describe("Scenario 5: Privacy Protection and Read-Only Integrations Status", () => {
    it("never includes case_id or tokens in requests/DOM and displays sanitized integration status", async () => {
      const page = await browser.newPage({ timezoneId: "Asia/Yekaterinburg" });
      const allInterceptedRequests = [];

      const adminUser = mockUser(1, [4], { username: "admin_security" });

      const mockIntegrations = {
        omnidesk: {
          configured: true,
          enabled: true,
          domain: "iridi.omnidesk.ru",
        },
        telegram: {
          configured: false,
          enabled: false,
          bot_username: null,
        },
        bitrix24: {
          configured: true,
          enabled: false,
          domain: "iridi.bitrix24.ru",
        },
      };

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const postData = req.postData();

        allInterceptedRequests.push({ url, postData });

        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(adminUser) });
        }
        if (url.endsWith("/admin/users")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([adminUser]) });
        }
        if (url.includes("/admin/integrations/status")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockIntegrations) });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not_found" }) });
      });

      // 1. Visit /admin and switch to Settings -> Integrations
      await page.goto(`${baseUrl}/admin`);
      await page.waitForSelector('[data-test="tab-settings"]');
      await page.click('[data-test="tab-settings"]');

      await page.waitForSelector('[data-test="subtab-integrations"]');
      await page.click('[data-test="subtab-integrations"]');

      await page.waitForSelector('[data-test="section-integrations"]');

      // Verify Omnidesk card
      const omnideskText = await page.textContent('[data-test="integration-omnidesk"]');
      assert.match(omnideskText, /Настроено/);
      assert.match(omnideskText, /Включено/);
      assert.match(omnideskText, /iridi\.omnidesk\.ru/);

      // Verify Telegram card
      const telegramText = await page.textContent('[data-test="integration-telegram"]');
      assert.match(telegramText, /Не настроено/);
      assert.match(telegramText, /Выключено/);

      // Verify Bitrix24 card
      const bitrixText = await page.textContent('[data-test="integration-bitrix24"]');
      assert.match(bitrixText, /Настроено/);
      assert.match(bitrixText, /Выключено/);

      // Verify NO secrets, API keys, tokens, or case_id in entire DOM content
      const fullHtml = await page.content();
      assert.equal(fullHtml.includes("case_id"), false, "HTML must never contain case_id");
      assert.equal(fullHtml.includes("api_key"), false, "HTML must never contain api_key");
      assert.equal(fullHtml.includes("secret_token"), false, "HTML must never contain secret_token");
      assert.equal(fullHtml.includes("bot_token"), false, "HTML must never contain bot_token");

      // Verify NO requests contained case_id or tokens in payload or query params
      for (const req of allInterceptedRequests) {
        assert.equal(req.url.includes("case_id"), false, `Request URL ${req.url} must not contain case_id`);
        if (req.postData) {
          assert.equal(req.postData.includes("case_id"), false, `Request body must not contain case_id`);
          assert.equal(req.postData.includes("token"), false, `Request body must not leak tokens`);
        }
      }

      await page.close();
    });
  });

  // -------------------------------------------------------------------------
  // Scenario 6: Visible Error Handling for 403, 409, and 422
  // -------------------------------------------------------------------------
  describe("Scenario 6: Display of 403 Forbidden, 409 Conflict, and 422 Validation Errors", () => {
    it("displays 403 forbidden error when operation is denied", async () => {
      const page = await browser.newPage();
      const adminUser = mockUser(1, [4]);

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const method = req.method();

        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(adminUser) });
        }
        if (url.endsWith("/admin/users")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([adminUser]) });
        }
        if (url.includes("/admin/settings/planning") && method === "PUT") {
          return route.fulfill({
            status: 403,
            contentType: "application/json",
            body: JSON.stringify({ detail: "insufficient_role" }),
          });
        }
        if (url.includes("/admin/settings/planning") && method === "GET") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              min_lead_minutes: 120,
              horizon_days: 14,
              default_duration_minutes: 60,
              min_duration_minutes: 30,
              max_duration_minutes: 720,
            }),
          });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not_found" }) });
      });

      await page.goto(`${baseUrl}/admin`);
      await page.waitForSelector('[data-test="tab-schedules"]');
      await page.click('[data-test="tab-schedules"]');

      await page.waitForSelector('[data-test="subtab-planning"]');
      await page.click('[data-test="subtab-planning"]');

      await page.waitForSelector('[data-test="btn-save-planning"]');
      await page.click('[data-test="btn-save-planning"]');

      await page.waitForSelector('[data-test="admin-error"]');
      assert.match(await page.textContent('[data-test="admin-error"]'), /Недостаточно прав для выполнения операции|Доступ запрещён/);

      await page.close();
    });

    it("displays 409 conflict error when username or admin constraint conflicts", async () => {
      const page = await browser.newPage();
      const adminUser = mockUser(1, [4]);

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const method = req.method();

        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(adminUser) });
        }
        if (url.endsWith("/admin/users") && method === "GET") {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([adminUser]) });
        }
        if (url.endsWith("/admin/users") && method === "POST") {
          return route.fulfill({
            status: 409,
            contentType: "application/json",
            body: JSON.stringify({ detail: "username_already_exists" }),
          });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not_found" }) });
      });

      await page.goto(`${baseUrl}/admin`);
      await page.waitForSelector('[data-test="btn-open-create-user"]');
      await page.click('[data-test="btn-open-create-user"]');

      await page.fill('[data-test="input-new-username"]', "existing_user");
      await page.fill('[data-test="input-new-password"]', "password123");
      await page.fill('[data-test="input-new-fullname"]', "Существующий Пользователь");
      await page.click('[data-test="btn-submit-create-user"]');

      await page.waitForSelector('[data-test="admin-error"]');
      assert.match(await page.textContent('[data-test="admin-error"]'), /Пользователь с таким логином уже существует/);

      await page.close();
    });

    it("displays 422 validation error when backend validation fails", async () => {
      const page = await browser.newPage();
      const adminUser = mockUser(1, [4]);

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const method = req.method();

        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(adminUser) });
        }
        if (url.endsWith("/admin/users") && method === "GET") {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([adminUser]) });
        }
        if (url.endsWith("/admin/users") && method === "POST") {
          return route.fulfill({
            status: 422,
            contentType: "application/json",
            body: JSON.stringify({
              detail: [{ loc: ["body", "omnidesk_staff_id"], msg: "Сотрудник с таким Omnidesk Staff ID не существует" }],
            }),
          });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not_found" }) });
      });

      await page.goto(`${baseUrl}/admin`);
      await page.waitForSelector('[data-test="btn-open-create-user"]');
      await page.click('[data-test="btn-open-create-user"]');

      await page.fill('[data-test="input-new-username"]', "invalid_staff_user");
      await page.fill('[data-test="input-new-password"]', "password123");
      await page.fill('[data-test="input-new-fullname"]', "Некорректный Staff ID");
      await page.fill('[data-test="input-new-staff-id"]', "999999");
      await page.click('[data-test="btn-submit-create-user"]');

      await page.waitForSelector('[data-test="admin-error"]');
      assert.match(await page.textContent('[data-test="admin-error"]'), /Проверьте данные: Сотрудник с таким Omnidesk Staff ID не существует/);

      await page.close();
    });
  });
});
