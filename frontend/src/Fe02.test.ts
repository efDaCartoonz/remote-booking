import { flushPromises, mount } from "@vue/test-utils";
import { slotInput } from "./testSlot";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App.vue";
import AppSidebar from "./AppSidebar.vue";

const originalFetch = globalThis.fetch;
const user = (id: number, role: number, additionalRoles: number[] = []) => ({
  id,
  username: `u${id}`,
  full_name: `User ${id}`,
  roles: [role, ...additionalRoles].map((r) => ({ id: r, name: `Role ${r}` })),
});
const card = (overrides: Record<string, unknown> = {}) => ({
  id: "00000000-0000-0000-0000-000000000001",
  number: "RDM-1",
  omnidesk_ticket_number: "123-456789",
  status: "assigned",
  status_label: "Назначено",
  planned_start_at: "2026-10-01T10:00:00Z",
  planned_end_at: "2026-10-01T11:00:00Z",
  planned_duration_minutes: 60,
  l1_owner_id: 11,
  l1_owner_name: "L1",
  l2_engineer_id: 22,
  l2_engineer_name: "L2",
  client_informed: false,
  criticality_code: 0,
  urgency_code: 0,
  overdue_flag: false,
  out_of_hours_flag: false,
  retroactive_flag: false,
  description: null,
  result_code: null,
  engineer_report: null,
  actual_start_at: null,
  actual_end_at: null,
  created_at: "2026-09-28T10:00:00Z",
  updated_at: "2026-09-28T10:00:00Z",
  ...overrides,
});
const ok = (body: unknown): Response => ({ ok: true, status: 200, json: async () => body }) as Response;
const fail = (status: number, detail: unknown): Response => ({ ok: false, status, json: async () => ({ detail }) }) as Response;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("FE-02 role workspaces", () => {
  it("distinguishes first and repeated failed cycles in manager attention", async () => {
    window.history.pushState({}, "", "/manager");
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(33, 3));
      if (path.includes("/manager/cards?")) {
        return ok({
          summary: { assigned: 0, confirmed: 0, rejected: 2, overdue: 0, urgent: 0, urgent_collision: 0 },
          items: [
            {
              public_id: "first",
              number: "RDM-FIRST",
              status: "rejected",
              status_label: "Отклонено",
              first_unsuccessful_cycle: true,
              repeated_unsuccessful_cycle: false,
              planned_start_at: "2026-10-01T10:00:00Z",
              planned_end_at: "2026-10-01T11:00:00Z",
              planned_duration_minutes: 60,
              l2_engineer_name: null,
              urgent: false,
              overdue: false,
            },
            {
              public_id: "again",
              number: "RDM-AGAIN",
              status: "rejected",
              status_label: "Отклонено",
              first_unsuccessful_cycle: false,
              repeated_unsuccessful_cycle: true,
              planned_start_at: "2026-10-01T10:00:00Z",
              planned_end_at: "2026-10-01T11:00:00Z",
              planned_duration_minutes: 60,
              l2_engineer_name: null,
              urgent: false,
              overdue: false,
            },
          ],
          limit: 100,
        });
      }
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).toContain("Первый неуспешный цикл");
    expect(wrapper.text()).toContain("Повторный неуспешный цикл");
  });

  it("loads the assigned L1 queue and sends a public-number create request", async () => {
    window.history.pushState({}, "", "/work");
    const calls: [string, RequestInit | undefined][] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push([path, init]);
      if (path.endsWith("/auth/me")) return ok(user(11, 1));
      if (path.includes("/cards/mine?role=l1")) return ok({ items: [card({ status: "rejected", status_label: "Отклонено" })], limit: 100 });
      if (path.endsWith("/cards/l1")) return fail(409, "active_card_exists_for_ticket");
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.find(".work-row").text()).toContain("Отклонено");
    expect(wrapper.find(".manager-stats").exists()).toBe(false);
    await wrapper.find(".role-create-form input[pattern]").setValue("123-456789");
    await slotInput(wrapper.find(".role-create-form")).setValue("2026-10-01T10:00");
    await wrapper.find(".role-create-form").trigger("submit");
    await flushPromises();
    const post = calls.find(([path, init]) => path.endsWith("/cards/l1") && init?.method === "POST");
    expect(post).toBeDefined();
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ case_number: "123-456789", planned_duration_minutes: 60 });
    expect(String(post?.[1]?.body)).not.toContain("case_id");
  });

  it("groups L1 cards into work sections with counters", async () => {
    window.history.pushState({}, "", "/work");
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(11, 1));
      if (path.includes("/cards/mine?role=l1")) {
        return ok({
          items: [
            card({ id: "a", number: "RDM-REJ", status: "rejected", status_label: "Отклонено" }),
            card({ id: "b", number: "RDM-ASG", status: "assigned", status_label: "Назначено" }),
            card({ id: "c", number: "RDM-PRG", status: "in_progress", status_label: "Выполняется" }),
            card({ id: "d", number: "RDM-OVD", status: "confirmed", status_label: "Подтверждено", overdue_flag: true }),
            card({ id: "e", number: "RDM-DONE", status: "completed", status_label: "Завершено" }),
            card({ id: "f", number: "RDM-CNL", status: "cancelled", status_label: "Отменено" }),
          ],
          limit: 100,
        });
      }
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();

    const tab = (key: string) => wrapper.find(`[data-test="mine-tab-${key}"]`);
    expect(tab("attention").text()).toContain("(2)");
    expect(tab("active").text()).toContain("(2)");
    expect(tab("done").text()).toContain("(2)");
    expect(tab("all").text()).toContain("(6)");

    // The default section holds the cards that need the L1's work: rejected and overdue ones.
    expect(wrapper.find(".work-list").text()).toContain("RDM-REJ");
    expect(wrapper.find(".work-list").text()).toContain("RDM-OVD");
    expect(wrapper.find(".work-list").text()).not.toContain("RDM-ASG");

    await tab("active").trigger("click");
    expect(wrapper.find(".work-list").text()).toContain("RDM-ASG");
    expect(wrapper.find(".work-list").text()).toContain("RDM-PRG");
    expect(wrapper.find(".work-list").text()).not.toContain("RDM-DONE");

    await tab("done").trigger("click");
    expect(wrapper.find(".work-list").text()).toContain("RDM-DONE");
    expect(wrapper.find(".work-list").text()).toContain("RDM-CNL");

    await tab("all").trigger("click");
    expect(wrapper.findAll(".work-list a")).toHaveLength(6);
  });

  it("switches queues and create forms for dual-role users", async () => {
    window.history.pushState({}, "", "/work");
    const calls: string[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path.endsWith("/auth/me")) return ok(user(15, 1, [2]));
      if (path.includes("/cards/mine?role=l1")) return ok({ items: [card({ number: "RDM-L1-QUEUE", status: "rejected", status_label: "Отклонено" })], limit: 100 });
      if (path.includes("/cards/mine?role=l2")) return ok({ items: [card({ number: "RDM-L2-QUEUE" })], limit: 100 });
      if (path.endsWith("/results")) return ok({ items: [{ code: 1, name: "Успешно" }] });
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).toContain("RDM-L1-QUEUE");
    expect(wrapper.text()).toContain("Создать карточку L1");

    const l2Toggle = wrapper.findAll(".manager-toggle button").find((btn) => btn.text() === "L2");
    expect(l2Toggle).toBeDefined();
    await l2Toggle?.trigger("click");
    await flushPromises();

    expect(calls.some((url) => url.includes("/cards/mine?role=l2"))).toBe(true);
    expect(wrapper.text()).toContain("RDM-L2-QUEUE");
    expect(wrapper.text()).toContain("Создать карточку L2");
    expect(wrapper.find(".role-create-form select:not([data-test^='role-slot'])").exists()).toBe(true);
  });

  it("forgets the remembered card route when the user signs out explicitly", async () => {
    window.history.pushState({}, "", "/");
    sessionStorage.setItem("rdm.return_to", "/cards/00000000-0000-0000-0000-000000000039");
    const calls: [string, RequestInit | undefined][] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push([path, init]);
      if (path.endsWith("/auth/me")) return ok(user(22, 2));
      if (path.includes("/cards/mine")) return ok({ items: [], limit: 100 });
      if (path.endsWith("/results")) return ok({ items: [] });
      if (path.endsWith("/auth/logout")) return { ok: true, status: 204, json: async () => ({}) } as Response;
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();

    wrapper.findComponent(AppSidebar).vm.$emit("logout");
    await flushPromises();

    expect(calls.some(([path, init]) => path.endsWith("/auth/logout") && init?.method === "POST")).toBe(true);
    expect(sessionStorage.getItem("rdm.return_to")).toBeNull();
    expect(wrapper.text()).toContain("Войдите");
  });

  it("drops a stale remembered card route once the session is valid", async () => {
    window.history.pushState({}, "", "/work");
    sessionStorage.setItem("rdm.return_to", "/cards/00000000-0000-0000-0000-000000000039");
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(22, 2));
      if (path.includes("/cards/mine")) return ok({ items: [], limit: 100 });
      if (path.endsWith("/results")) return ok({ items: [] });
      return fail(404, "missing");
    });
    mount(App);
    await flushPromises();
    expect(sessionStorage.getItem("rdm.return_to")).toBeNull();
  });

  it("submits L2 urgent and retroactive create payloads with expected fields", async () => {
    window.history.pushState({}, "", "/work");
    const calls: [string, RequestInit | undefined][] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push([path, init]);
      if (path.endsWith("/auth/me")) return ok(user(22, 2));
      if (path.includes("/cards/mine?role=l2")) return ok({ items: [], limit: 100 });
      if (path.endsWith("/results")) return ok({ items: [{ code: 9, name: "Выполнено успешно" }] });
      if (path.endsWith("/cards/l2/urgent") || path.endsWith("/cards/l2/retroactive")) return ok(card({ id: "new-card-id" }));
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();

    // 1. Urgent scenario
    await wrapper.find(".role-create-form input[pattern]").setValue("222-333444");
    await slotInput(wrapper.find(".role-create-form")).setValue("2026-10-01T14:00");
    await wrapper.find(".role-create-form select:not([data-test^='role-slot'])").setValue("urgent");
    await flushPromises();
    const urgentReasonInput = wrapper.findAll(".role-create-form label").find((l) => l.text().includes("Причина срочности"))?.find("input");
    expect(urgentReasonInput).toBeDefined();
    await urgentReasonInput?.setValue("Авария на объекте");
    await wrapper.find(".role-create-form").trigger("submit");
    await flushPromises();

    const urgentPost = calls.find(([path, init]) => path.endsWith("/cards/l2/urgent") && init?.method === "POST");
    expect(urgentPost).toBeDefined();
    expect(JSON.parse(String(urgentPost?.[1]?.body))).toMatchObject({
      case_number: "222-333444",
      planned_duration_minutes: 60,
      urgent_reason: "Авария на объекте",
    });

    // 2. Retroactive scenario
    await wrapper.find(".role-create-form select:not([data-test^='role-slot'])").setValue("retroactive");
    await flushPromises();
    const resultSelect = wrapper.findAll(".role-create-form select:not([data-test^='role-slot'])").find((s) => s.text().includes("Выполнено успешно"));
    await resultSelect?.setValue("9");
    const reportTextarea = wrapper.findAll(".role-create-form label").find((l) => l.text().includes("Отчёт"))?.find("textarea");
    expect(reportTextarea).toBeDefined();
    await reportTextarea?.setValue("Работы завершены ретроспективно");
    await wrapper.find(".role-create-form").trigger("submit");
    await flushPromises();

    const retroPost = calls.find(([path, init]) => path.endsWith("/cards/l2/retroactive") && init?.method === "POST");
    expect(retroPost).toBeDefined();
    expect(JSON.parse(String(retroPost?.[1]?.body))).toMatchObject({
      case_number: "222-333444",
      result_code: 9,
      engineer_report: "Работы завершены ретроспективно",
    });
  });

  it("shows L2 decisions only for the assigned engineer and formats manager decision panel", async () => {
    window.history.pushState({}, "", "/cards/00000000-0000-0000-0000-000000000001");
    // 1. Unassigned L2
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(23, 2));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.includes("/cards/")) return ok(card());
      return fail(404, "missing");
    });
    let wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).toContain("Решение доступно только назначенному инженеру L2");
    expect(wrapper.text()).not.toContain("Подтвердить назначение");
    expect(wrapper.text()).not.toContain("Начать выполнение");
    expect(wrapper.text()).not.toContain("Назначить L2");

    // 2. Assigned L2
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(22, 2));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.includes("/cards/")) return ok(card({ l2_engineer_id: 22 }));
      return fail(404, "missing");
    });
    wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).toContain("Решение L2");
    expect(wrapper.findAll("button").some((b) => b.text().includes("Подтвердить назначение"))).toBe(true);
    expect(wrapper.findAll("button").some((b) => b.text() === "Отклонить")).toBe(true);

    // 3. Manager
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(33, 3));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.includes("/manager/l2-options")) return ok({ items: [] });
      if (path.includes("/cards/")) return ok(card({ l2_engineer_id: 22 }));
      return fail(404, "missing");
    });
    wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).toContain("Решение руководителя");
    expect(wrapper.findAll("button").some((b) => b.text() === "Отклонить за инженера")).toBe(true);
  });

  it("handles self-assign visibility and submission for L2", async () => {
    window.history.pushState({}, "", "/cards/00000000-0000-0000-0000-000000000001");
    const calls: [string, RequestInit | undefined][] = [];
    // 1. Unassigned L2 viewing a card assigned to someone else
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push([path, init]);
      if (path.endsWith("/auth/me")) return ok(user(24, 2));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.endsWith("/assign") && init?.method === "POST") return ok(card({ l2_engineer_id: 24 }));
      if (path.includes("/cards/")) return ok(card({ l2_engineer_id: 22, status: "created" }));
      return fail(404, "missing");
    });
    let wrapper = mount(App);
    await flushPromises();

    expect(wrapper.text()).toContain("Назначить себя L2");
    await wrapper.find("section.panel.actions input").setValue("Беру в работу");
    await wrapper.findAll("form").find((f) => f.text().includes("Назначить себя"))?.trigger("submit");
    await flushPromises();

    const assignCall = calls.find(([path, init]) => path.endsWith("/assign") && init?.method === "POST");
    expect(assignCall).toBeDefined();
    expect(JSON.parse(String(assignCall?.[1]?.body))).toEqual({
      l2_engineer_id: 24,
      comment: "Беру в работу",
    });

    // 2. Already assigned L2 on this card should NOT see the self-assign panel
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(22, 2));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.includes("/cards/")) return ok(card({ l2_engineer_id: 22 }));
      return fail(404, "missing");
    });
    wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).not.toContain("Назначить себя L2");
  });

  it("restricts completion on completed_pending_result to assigned L2 only", async () => {
    window.history.pushState({}, "", "/cards/00000000-0000-0000-0000-000000000001");
    // 1. Manager (not assigned L2) cannot complete or see completion form
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(33, 3));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.includes("/cards/")) return ok(card({ status: "completed_pending_result", status_label: "Окончено", l2_engineer_id: 22 }));
      return fail(404, "missing");
    });
    let wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).not.toContain("Завершить");
    expect(wrapper.find("form textarea[required]").exists()).toBe(false);

    // 2. Assigned L2 can complete missing result
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(22, 2));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.endsWith("/results")) return ok({ items: [{ code: 1, name: "Успешно" }] });
      if (path.includes("/cards/")) return ok(card({ status: "completed_pending_result", status_label: "Окончено", l2_engineer_id: 22 }));
      return fail(404, "missing");
    });
    wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).toContain("Завершить");
    expect(wrapper.find("form textarea[required]").exists()).toBe(true);
  });

  it("allows ending in-progress card without result via end-pending-result action", async () => {
    window.history.pushState({}, "", "/cards/00000000-0000-0000-0000-000000000001");
    const calls: [string, RequestInit | undefined][] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push([path, init]);
      if (path.endsWith("/auth/me")) return ok(user(22, 2));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.endsWith("/results")) return ok({ items: [{ code: 1, name: "Успешно" }] });
      if (path.endsWith("/end-pending-result") && init?.method === "POST") {
        return ok(card({ status: "completed_pending_result", status_label: "Окончено" }));
      }
      if (path.includes("/cards/")) return ok(card({ status: "in_progress", status_label: "Выполняется", l2_engineer_id: 22 }));
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();

    const endBtn = wrapper.findAll("button").find((b) => b.text() === "Окончить");
    expect(endBtn).toBeDefined();
    await endBtn?.trigger("click");
    await flushPromises();

    const endCall = calls.find(([path, init]) => path.endsWith("/end-pending-result") && init?.method === "POST");
    expect(endCall).toBeDefined();
  });

  it("displays notification 403 error message instead of false none", async () => {
    window.history.pushState({}, "", "/cards/00000000-0000-0000-0000-000000000001");
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(22, 2));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return fail(403, "forbidden");
      if (path.includes("/cards/")) return ok(card());
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).toContain("Доступ к уведомлениям ограничен (403)");
    expect(wrapper.text()).not.toContain("Уведомлений пока нет");
  });

  it("validates cancel and reschedule reasons and refreshes card on 409 conflict", async () => {
    window.history.pushState({}, "", "/cards/00000000-0000-0000-0000-000000000001");
    let cardVersion = "RDM-V1";
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(33, 3));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.includes("/manager/l2-options")) return ok({ items: [] });
      if (path.endsWith("/cancel") && init?.method === "POST") {
        cardVersion = "RDM-V2-CONFLICTED";
        return fail(409, "action_not_allowed_for_status");
      }
      if (path.endsWith("/l1/reschedule") && init?.method === "POST") {
        cardVersion = "RDM-V3-RESCHEDULE-CONFLICTED";
        return fail(409, "status_transition_not_allowed");
      }
      if (path.includes("/cards/")) return ok(card({ description: cardVersion }));
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();

    // Cancel validation and 409 conflict
    const cancelForm = wrapper.findAll("form").find((f) => f.text().includes("Отменить карточку"));
    expect(cancelForm).toBeDefined();
    await cancelForm?.find("input").setValue("Клиент передумал");
    await cancelForm?.trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("Действие недоступно для текущего статуса карточки");
    expect(wrapper.text()).toContain("RDM-V2-CONFLICTED");

    // Reschedule validation and 409 conflict
    const rescheduleForm = wrapper.find(".reschedule-form");
    await slotInput(rescheduleForm).setValue("2026-10-02T12:00");
    const rescheduleReasonInput = wrapper.findAll(".reschedule-form label").find((l) => l.text().includes("Причина переноса"))?.find("input");
    expect(rescheduleReasonInput).toBeDefined();
    await rescheduleReasonInput?.setValue("Перенос по согласованию");
    await rescheduleForm.trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("Действие недоступно для текущего статуса карточки");
    expect(wrapper.text()).toContain("RDM-V3-RESCHEDULE-CONFLICTED");
  });

  it("shows backend policy errors without exposing server details", async () => {
    window.history.pushState({}, "", "/cards/00000000-0000-0000-0000-000000000001");
    let status = 403;
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(22, 2));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.endsWith("/confirm") && init?.method === "POST") return fail(status, "case_id=private");
      if (path.includes("/cards/")) return ok(card());
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();
    const confirm = wrapper.findAll("button").find((button) => button.text().includes("Подтвердить назначение"));
    expect(confirm).toBeDefined();
    for (const [code, text] of [[403, "Недостаточно прав"], [409, "Карточка уже изменилась"], [422, "Проверьте введённые данные"]] as const) {
      status = code;
      await confirm?.trigger("click");
      await flushPromises();
      expect(wrapper.text()).toContain(text);
      expect(wrapper.html()).not.toContain("case_id");
    }
    status = 401;
    await confirm?.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Вход");
  });

  it("lets the assigned L1 select the post-informed reminder interval", async () => {
    window.history.pushState({}, "", "/cards/00000000-0000-0000-0000-000000000001");
    const posts: RequestInit[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(11, 1));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.endsWith("/l1/reminder-interval")) {
        if (init?.method === "POST") {
          posts.push(init);
          return ok({ interval_minutes: 30 });
        }
        return ok({ interval_minutes: 10, can_change: true });
      }
      if (path.includes("/cards/")) return ok(card({ status: "rejected", status_label: "Отклонено", client_informed: true }));
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).toContain("Интервал напоминаний");
    await wrapper.find(".inline-form select").setValue("30");
    await wrapper.find(".inline-form").trigger("submit");
    await flushPromises();
    expect(JSON.parse(String(posts[0].body))).toEqual({ interval_minutes: 30 });
  });

  it("offers manager reassignment and rejection, but not L1 follow-up", async () => {
    window.history.pushState({}, "", "/cards/00000000-0000-0000-0000-000000000001");
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/auth/me")) return ok(user(33, 3));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.includes("/manager/l2-options")) return ok({ items: [{ user_id: 22, display_name: "L2", available: true, reason_code: null }] });
      if (path.includes("/cards/")) return ok(card());
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).toContain("Назначить L2");
    expect(wrapper.text()).toContain("Отклонить");
    expect(wrapper.text()).toContain("Отменить карточку");
    expect(wrapper.text()).not.toContain("Клиент проинформирован");
  });

  it("submits L2 start and completion through the approved card actions", async () => {
    window.history.pushState({}, "", "/cards/00000000-0000-0000-0000-000000000001");
    const calls: [string, RequestInit | undefined][] = [];
    let status = "confirmed";
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push([path, init]);
      if (path.endsWith("/auth/me")) return ok(user(22, 2));
      if (path.endsWith("/history")) return ok([]);
      if (path.endsWith("/notifications")) return ok({ items: [] });
      if (path.endsWith("/results")) return ok({ items: [{ code: 7, name: "Успешно" }] });
      if (path.endsWith("/start") && init?.method === "POST") {
        status = "in_progress";
        return ok(card({ status }));
      }
      if (path.endsWith("/complete") && init?.method === "POST") return fail(422, "missing_result");
      if (path.includes("/cards/")) return ok(card({ status, status_label: status }));
      return fail(404, "missing");
    });
    const wrapper = mount(App);
    await flushPromises();
    const start = wrapper.findAll("button").find((button) => button.text() === "Начать выполнение");
    await start?.trigger("click");
    await flushPromises();
    expect(calls.some(([path, init]) => path.endsWith("/start") && init?.method === "POST")).toBe(true);
    expect(wrapper.text()).toContain("Завершить");
    expect(calls.some(([path]) => path.endsWith("/results"))).toBe(true);
    const selects = wrapper.findAll("select");
    await selects.find((select) => select.text().includes("Успешно"))?.setValue("7");
    await wrapper.find("textarea[required]").setValue("Проверено подключение");
    await wrapper.find("input[type='number']").setValue("45");
    const complete = wrapper.findAll("button").find((button) => button.text() === "Завершить");
    expect(complete).toBeDefined();
    await wrapper.findAll("form").find((form) => form.text().includes("Завершить"))?.trigger("submit");
    await flushPromises();
    const post = calls.find(([path, init]) => path.endsWith("/complete") && init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ result_code: 7, engineer_report: "Проверено подключение", actual_duration_minutes: 45 });
  });

  it("blocks direct manager-create access for L1", async () => {
    window.history.pushState({}, "", "/manager/cards/new");
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => (String(input).endsWith("/auth/me") ? ok(user(11, 1)) : fail(404, "missing")));
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.text()).toContain("Доступ запрещён");
    expect(wrapper.find(".create-form").exists()).toBe(false);
  });
});
