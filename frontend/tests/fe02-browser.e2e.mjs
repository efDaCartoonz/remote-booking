import assert from "node:assert/strict";
import { after, before, beforeEach, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const frontendRoot = resolve(__dirname, "..");

function user(id, roleId, additionalRoles = []) {
  return {
    id,
    username: `user_${id}`,
    full_name: `User ${id}`,
    roles: [roleId, ...additionalRoles].map((r) => ({ id: r, name: `Role ${r}` })),
  };
}

function card(overrides = {}) {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    number: "RDM-100",
    omnidesk_ticket_number: "123-456789",
    status: "assigned",
    status_label: "Назначено",
    planned_start_at: "2026-10-01T10:00:00Z",
    planned_end_at: "2026-10-01T11:00:00Z",
    planned_duration_minutes: 60,
    l1_owner_id: 11,
    l1_owner_name: "L1 Specialist",
    l2_engineer_id: 22,
    l2_engineer_name: "L2 Engineer",
    client_informed: false,
    criticality_code: 0,
    urgency_code: 0,
    overdue_flag: false,
    out_of_hours_flag: false,
    retroactive_flag: false,
    description: "Standard task description",
    result_code: null,
    engineer_report: null,
    actual_start_at: null,
    actual_end_at: null,
    created_at: "2026-09-28T10:00:00Z",
    updated_at: "2026-09-28T10:00:00Z",
    ...overrides,
  };
}

describe("RDM FE-02 Bounded Playwright Browser Suite", () => {
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

  describe("Role L1 UI actions and boundaries", () => {
    it("allows L1 work list queue, creation without case_id, and follow-up with reminder interval", async () => {
      const page = await browser.newPage();
      const interceptedRequests = [];

      let currentCard = card({
        status: "rejected",
        status_label: "Отклонено",
        l1_owner_id: 11,
        client_informed: false,
      });

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const method = req.method();
        const postData = req.postData();

        interceptedRequests.push({ url, method, body: postData });

        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user(11, 1)) });
        }
        if (url.includes("/cards/mine?role=l1")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [currentCard], limit: 100 }),
          });
        }
        if (url.includes("/cards/l1") && method === "POST") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ id: "00000000-0000-0000-0000-000000000002", number: "RDM-102" })),
          });
        }
        if (url.includes("/history")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
        }
        if (url.includes("/notifications")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
        }
        if (url.includes("/l1/client-informed") && method === "POST") {
          currentCard = { ...currentCard, client_informed: true };
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(currentCard) });
        }
        if (url.includes("/l1/reminder-interval")) {
          if (method === "POST") {
            return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ interval_minutes: 30 }) });
          }
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ interval_minutes: 10 }) });
        }
        if (url.includes("/cards/00000000-0000-0000-0000-000000000001")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(currentCard) });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) });
      });

      // 1. Visit work list
      await page.goto(`${baseUrl}/work`);
      await page.waitForSelector(".work-list");
      const workListText = await page.textContent(".work-list");
      assert.match(workListText, /RDM-100/);
      assert.match(workListText, /Отклонено/);

      // Create L1 card
      await page.fill(".role-create-form input[pattern]", "111-222333");
      await page.selectOption(".role-create-form [data-test='role-slot-date']", { index: 2 });
      await page.selectOption(".role-create-form [data-test='role-slot-time']", "10:00");
      await page.fill(".role-create-form textarea", "L1 client card");
      await Promise.all([page.waitForURL(/\/cards\//), page.click(".role-create-form button")]);

      // Verify L1 create payload
      const l1CreateCall = interceptedRequests.find((r) => r.url.endsWith("/cards/l1") && r.method === "POST");
      assert.ok(l1CreateCall, "L1 create call should be made");
      const createBody = JSON.parse(l1CreateCall.body);
      assert.equal(createBody.case_number, "111-222333");
      assert.equal(createBody.planned_duration_minutes, 60);
      assert.equal(createBody.description, "L1 client card");
      assert.equal(l1CreateCall.body.includes("case_id"), false, "case_id must not leak in request body");

      // 2. Open rejected card for L1 follow-up
      await page.goto(`${baseUrl}/cards/00000000-0000-0000-0000-000000000001`);
      await page.waitForSelector("section.panel.actions");
      assert.match(await page.textContent("body"), /Сопровождение L1/);

      // Click "Клиент проинформирован"
      const informedBtn = page.getByRole("button", { name: "Клиент проинформирован" });
      await informedBtn.click();

      // Reminder interval selector appears
      await page.waitForSelector(".inline-form select");
      await page.selectOption(".inline-form select", "30");
      await page.click(".inline-form button");

      // Verify reminder interval call
      const intervalCall = interceptedRequests.find((r) => r.url.includes("/l1/reminder-interval") && r.method === "POST");
      assert.ok(intervalCall, "Reminder interval POST should be made");
      assert.deepEqual(JSON.parse(intervalCall.body), { interval_minutes: 30 });

      // Check DOM for no case_id leakage
      const html = await page.content();
      assert.equal(html.includes("case_id"), false, "case_id must not leak in HTML");

      await page.close();
    });

    it("forbids L1 from seeing or invoking L2 decisions, self-assign, and manager dashboard/create", async () => {
      const page = await browser.newPage();

      await page.route("**/api/v1/**", async (route) => {
        const url = route.request().url();
        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user(11, 1)) });
        }
        if (url.includes("/history") || url.includes("/notifications")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
        }
        if (url.includes("/cards/00000000-0000-0000-0000-000000000001")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ status: "assigned", l2_engineer_id: 22 })),
          });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) });
      });

      // 1. On assigned card, L1 must not see L2 decisions or manager actions
      await page.goto(`${baseUrl}/cards/00000000-0000-0000-0000-000000000001`);
      await page.waitForSelector(".shell");
      const bodyText = await page.textContent("body");

      assert.equal(bodyText.includes("Подтвердить назначение"), false);
      assert.equal(bodyText.includes("Начать выполнение"), false);
      assert.equal(bodyText.includes("Назначить себя"), false);
      assert.equal(bodyText.includes("Решение руководителя"), false);
      assert.equal(bodyText.includes("Отклонить за инженера"), false);
      assert.equal(bodyText.includes("Назначить L2"), false);

      // 2. Direct navigation to manager URL
      await page.goto(`${baseUrl}/manager`);
      await page.waitForSelector("body");
      const managerText = await page.textContent("body");
      assert.match(managerText, /Доступ к панели руководителя запрещён \(403\)/);

      // 3. Direct navigation to manager new card URL
      await page.goto(`${baseUrl}/manager/cards/new`);
      await page.waitForSelector("body");
      const managerNewText = await page.textContent("body");
      assert.match(managerNewText, /Доступ запрещён/);
      assert.equal(await page.locator(".create-form").count(), 0);

      await page.close();
    });
  });

  describe("Role L2 UI actions and boundaries", () => {
    it("allows L2 decisions, start, complete with report, and self-assign on available card", async () => {
      const page = await browser.newPage();
      const interceptedRequests = [];

      let cardStatus = "assigned";
      let assignedL2Id = 22;

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const method = req.method();
        const postData = req.postData();

        interceptedRequests.push({ url, method, body: postData });

        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user(22, 2)) });
        }
        if (url.includes("/cards/mine?role=l2")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [card({ l2_engineer_id: 22 })], limit: 100 }),
          });
        }
        if (url.includes("/cards/results")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [{ code: 1, name: "Успешно" }, { code: 2, name: "Частично успешно" }] }),
          });
        }
        if (url.includes("/history") || url.includes("/notifications")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
        }
        if (url.includes("/confirm") && method === "POST") {
          cardStatus = "confirmed";
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ status: cardStatus, status_label: "Подтверждено", l2_engineer_id: 22 })),
          });
        }
        if (url.includes("/start") && method === "POST") {
          cardStatus = "in_progress";
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ status: cardStatus, status_label: "Выполняется", l2_engineer_id: 22 })),
          });
        }
        if (url.includes("/complete") && method === "POST") {
          cardStatus = "completed";
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ status: cardStatus, status_label: "Завершено", l2_engineer_id: 22 })),
          });
        }
        if (url.includes("/cards/00000000-0000-0000-0000-000000000001")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ status: cardStatus, status_label: cardStatus, l2_engineer_id: assignedL2Id })),
          });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) });
      });

      // 1. Visit assigned card as assigned L2
      await page.goto(`${baseUrl}/cards/00000000-0000-0000-0000-000000000001`);
      await page.waitForSelector("section.panel.actions");
      assert.match(await page.textContent("body"), /Решение L2/);

      // Confirm assignment
      const confirmBtn = page.getByRole("button", { name: "Подтвердить назначение" });
      await confirmBtn.click();

      // Start card
      await page.waitForSelector("button:has-text('Начать выполнение')");
      await page.click("button:has-text('Начать выполнение')");

      // Complete card with results and report
      await page.waitForSelector("form textarea[required]");
      await page.selectOption("select", "1");
      await page.fill("textarea[required]", "Connection verified and test completed");
      await page.fill("input[type='number']", "45");
      await page.click("button:has-text('Завершить')");

      const completeCall = interceptedRequests.find((r) => r.url.endsWith("/complete") && r.method === "POST");
      assert.ok(completeCall, "Complete POST should be dispatched");
      const completeBody = JSON.parse(completeCall.body);
      assert.equal(completeBody.result_code, 1);
      assert.equal(completeBody.engineer_report, "Connection verified and test completed");
      assert.equal(completeBody.actual_duration_minutes, 45);

      await page.close();
    });

    it("allows self-assign for unassigned L2 and forbids L1 follow-up and manager controls", async () => {
      const page = await browser.newPage();
      const interceptedRequests = [];

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const method = req.method();

        interceptedRequests.push({ url, method, body: req.postData() });

        if (url.includes("/auth/me")) {
          // L2 engineer with id 24 (viewing card assigned to 22)
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user(24, 2)) });
        }
        if (url.includes("/history") || url.includes("/notifications")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
        }
        if (url.includes("/assign") && method === "POST") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ l2_engineer_id: 24, status: "assigned" })),
          });
        }
        if (url.includes("/cards/00000000-0000-0000-0000-000000000001")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ status: "assigned", l2_engineer_id: 22 })),
          });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) });
      });

      await page.goto(`${baseUrl}/cards/00000000-0000-0000-0000-000000000001`);
      await page.getByText("Решение доступно только назначенному инженеру L2").waitFor();

      const bodyText = await page.textContent("body");
      // Unassigned L2 sees hint & self-assign panel, but not confirm/reject/manager controls
      assert.match(bodyText, /Решение доступно только назначенному инженеру L2/);
      assert.match(bodyText, /Назначить себя L2/);
      assert.equal(bodyText.includes("Подтвердить назначение"), false);
      assert.equal(bodyText.includes("Клиент проинформирован"), false);
      assert.equal(bodyText.includes("Решение руководителя"), false);
      assert.equal(bodyText.includes("Отклонить за инженера"), false);

      // Perform self-assignment
      await page.fill("form:has-text('Назначить себя') input", "Taking over ticket");
      await page.click("form:has-text('Назначить себя') button");

      const assignCall = interceptedRequests.find((r) => r.url.endsWith("/assign") && r.method === "POST");
      assert.ok(assignCall, "Self-assign POST should be made");
      const assignBody = JSON.parse(assignCall.body);
      assert.equal(assignBody.l2_engineer_id, 24);
      assert.equal(assignBody.comment, "Taking over ticket");

      await page.close();
    });
  });

  describe("Role Manager UI actions and boundaries", () => {
    it("renders manager attention list with first and repeated failed cycles, reassignment, and reject-on-behalf", async () => {
      const page = await browser.newPage();
      const interceptedRequests = [];

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const method = req.method();

        interceptedRequests.push({ url, method, body: req.postData() });

        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user(33, 3)) });
        }
        if (url.includes("/manager/cards?")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              summary: { assigned: 1, confirmed: 2, rejected: 2, overdue: 1, urgent: 1, urgent_collision: 0 },
              items: [
                {
                  public_id: "00000000-0000-0000-0000-000000000001",
                  number: "RDM-CYCLE-1",
                  omnidesk_ticket_number: "111-222333",
                  status: "rejected",
                  status_label: "Отклонено",
                  first_unsuccessful_cycle: true,
                  repeated_unsuccessful_cycle: false,
                  planned_start_at: "2026-10-01T10:00:00Z",
                  planned_end_at: "2026-10-01T11:00:00Z",
                  planned_duration_minutes: 60,
                  l1_owner_name: "L1 Lead",
                  l2_engineer_name: null,
                  urgent: false,
                  overdue: false,
                  out_of_hours: false,
                },
                {
                  public_id: "00000000-0000-0000-0000-000000000002",
                  number: "RDM-CYCLE-2",
                  omnidesk_ticket_number: "444-555666",
                  status: "rejected",
                  status_label: "Отклонено",
                  first_unsuccessful_cycle: false,
                  repeated_unsuccessful_cycle: true,
                  planned_start_at: "2026-10-01T12:00:00Z",
                  planned_end_at: "2026-10-01T13:00:00Z",
                  planned_duration_minutes: 60,
                  l1_owner_name: "L1 Lead",
                  l2_engineer_name: null,
                  urgent: true,
                  overdue: false,
                  out_of_hours: false,
                },
              ],
              limit: 100,
            }),
          });
        }
        if (url.includes("/manager/l2-options")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              items: [
                { user_id: 22, display_name: "L2 Alex", available: true, reason_code: null },
                { user_id: 23, display_name: "L2 Bob", available: false, reason_code: "schedule_or_conflict" },
              ],
            }),
          });
        }
        if (url.includes("/history") || url.includes("/notifications")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
        }
        if (url.includes("/cards/00000000-0000-0000-0000-000000000001")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ status: "assigned", l2_engineer_id: 22 })),
          });
        }
        if (url.includes("/reject") && method === "POST") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ status: "rejected" })),
          });
        }
        if (url.includes("/assign") && method === "POST") {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ status: "assigned", l2_engineer_id: 22 })),
          });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) });
      });

      // 1. Visit manager dashboard
      await page.goto(`${baseUrl}/manager`);
      await page.waitForSelector(".manager-stats");
      const attentionText = await page.textContent("body");
      assert.match(attentionText, /Требуют внимания/);
      assert.match(attentionText, /Первый неуспешный цикл/);
      assert.match(attentionText, /Повторный неуспешный цикл/);

      // 2. Open card as Manager
      await page.goto(`${baseUrl}/cards/00000000-0000-0000-0000-000000000001`);
      await page.waitForSelector("section.panel.actions");
      const cardPageText = await page.textContent("body");
      assert.match(cardPageText, /Решение руководителя/);
      assert.match(cardPageText, /Отклонить за инженера/);
      assert.match(cardPageText, /Назначить L2/);

      // Manager reject on behalf
      await page.fill("section.panel.actions:has-text('Решение руководителя') input", "Rejected by supervisor");
      await page.click("button:has-text('Отклонить за инженера')");

      const rejectCall = interceptedRequests.find((r) => r.url.endsWith("/reject") && r.method === "POST");
      assert.ok(rejectCall, "Manager reject POST should be dispatched");
      assert.deepEqual(JSON.parse(rejectCall.body), { rejection_reason: "Rejected by supervisor" });

      // Manager reassigns L2
      await page.selectOption("section.panel.actions:has-text('Назначить L2') select", "22");
      await page.fill("section.panel.actions:has-text('Назначить L2') input", "Manager reassigning to available L2");
      await page.click("section.panel.actions:has-text('Назначить L2') button:has-text('Назначить')");

      const assignCall = interceptedRequests.find((r) => r.url.endsWith("/assign") && r.method === "POST");
      assert.ok(assignCall, "Manager assign POST should be dispatched");
      assert.deepEqual(JSON.parse(assignCall.body), {
        l2_engineer_id: 22,
        comment: "Manager reassigning to available L2",
      });

      await page.close();
    });

    it("forbids manager from submitting completion when card is in completed_pending_result status and forbids L1 follow-up", async () => {
      const page = await browser.newPage();

      await page.route("**/api/v1/**", async (route) => {
        const url = route.request().url();
        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user(33, 3)) });
        }
        if (url.includes("/history") || url.includes("/notifications") || url.includes("/manager/l2-options")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
        }
        if (url.includes("/cards/00000000-0000-0000-0000-000000000001")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ status: "completed_pending_result", status_label: "Окончено", l2_engineer_id: 22 })),
          });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) });
      });

      await page.goto(`${baseUrl}/cards/00000000-0000-0000-0000-000000000001`);
      await page.waitForSelector(".shell");

      const bodyText = await page.textContent("body");
      // Manager cannot complete or follow-up
      assert.equal(bodyText.includes("Завершить"), false);
      assert.equal(bodyText.includes("Клиент проинформирован"), false);
      assert.equal(bodyText.includes("Интервал напоминаний"), false);
      assert.equal(await page.locator("form textarea[required]").count(), 0);

      await page.close();
    });
  });

  describe("Visible 401/403/409/422 status handling and case_id protection", () => {
    it("handles 401 unauthorized session by redirecting to login form", async () => {
      const page = await browser.newPage();

      await page.route("**/api/v1/**", async (route) => {
        const url = route.request().url();
        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "unauthorized" }) });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "missing" }) });
      });

      await page.goto(`${baseUrl}/cards/00000000-0000-0000-0000-000000000001`);
      await page.waitForSelector("form");
      assert.equal(await page.textContent("h1"), "Вход");
      assert.equal(await page.locator("input[autocomplete='username']").count(), 1);

      await page.close();
    });

    it("handles 403 forbidden and 422 validation errors with clear user messages", async () => {
      const page = await browser.newPage();
      let actionStatus = 403;
      let actionDetail = "action_forbidden";

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const method = req.method();

        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user(22, 2)) });
        }
        if (url.includes("/history") || url.includes("/notifications")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
        }
        if (url.includes("/confirm") && method === "POST") {
          return route.fulfill({ status: actionStatus, contentType: "application/json", body: JSON.stringify({ detail: actionDetail }) });
        }
        if (url.includes("/cards/00000000-0000-0000-0000-000000000001")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(card({ status: "assigned", l2_engineer_id: 22 })) });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "missing" }) });
      });

      await page.goto(`${baseUrl}/cards/00000000-0000-0000-0000-000000000001`);
      await page.waitForSelector("button:has-text('Подтвердить назначение')");

      // 1. Trigger 403
      actionStatus = 403;
      actionDetail = "action_forbidden";
      await page.click("button:has-text('Подтвердить назначение')");
      await page.waitForSelector(".action-error");
      assert.match(await page.textContent(".action-error"), /Недостаточно прав для этого действия/);

      // 2. Trigger 422 validation error
      actionStatus = 422;
      actionDetail = [{ msg: "Неверный формат данных" }];
      await page.click("button:has-text('Подтвердить назначение')");
      await page.waitForSelector(".action-error");
      assert.match(await page.textContent(".action-error"), /Проверьте данные: Неверный формат данных/);

      await page.close();
    });

    it("handles 409 conflict and refreshes card state while preserving privacy", async () => {
      const page = await browser.newPage();
      let cardVersion = "Card Original State";

      await page.route("**/api/v1/**", async (route) => {
        const req = route.request();
        const url = req.url();
        const method = req.method();

        if (url.includes("/auth/me")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user(33, 3)) });
        }
        if (url.includes("/history") || url.includes("/notifications") || url.includes("/manager/l2-options")) {
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
        }
        if (url.includes("/cancel") && method === "POST") {
          cardVersion = "Card Modified By Concurrency";
          return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "action_not_allowed_for_status" }) });
        }
        if (url.includes("/cards/00000000-0000-0000-0000-000000000001")) {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(card({ description: cardVersion, status: "assigned", l2_engineer_id: 22 })),
          });
        }
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "missing" }) });
      });

      await page.goto(`${baseUrl}/cards/00000000-0000-0000-0000-000000000001`);
      await page.waitForSelector("button:has-text('Отменить карточку')");

      await page.fill("label:has-text('Причина отмены') input", "Client requested cancellation");
      await page.click("button:has-text('Отменить карточку')");

      await page.waitForSelector(".action-error");
      const errorText = await page.textContent(".action-error");
      assert.match(errorText, /Действие недоступно для текущего статуса карточки/);

      // Verify refreshed card description
      const updatedBody = await page.textContent("body");
      assert.match(updatedBody, /Card Modified By Concurrency/);
      assert.equal(updatedBody.includes("case_id"), false, "case_id must not leak in UI");

      await page.close();
    });
  });
});
