import { DOMWrapper, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.vue";

describe("App manager view", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    window.history.pushState({}, "", "/manager");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders manager summary with urgent and urgent_collision stats", async () => {
    const mockUser = {
      id: 1,
      username: "manager",
      full_name: "Руководитель Поддержки",
      roles: [{ id: 3, name: "Руководитель" }],
    };

    const mockManagerData = {
      summary: {
        assigned: 10,
        confirmed: 7,
        rejected: 2,
        overdue: 1,
        urgent: 5,
        urgent_collision: 3,
      },
      items: [],
      limit: 50,
    };

    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return {
          ok: true,
          status: 200,
          json: async () => mockUser,
        } as Response;
      }
      if (url.includes("/api/v1/manager/cards")) {
        return {
          ok: true,
          status: 200,
          json: async () => mockManagerData,
        } as Response;
      }
      return {
        ok: false,
        status: 404,
        json: async () => ({}),
      } as Response;
    });

    const wrapper = mount(App);
    await flushPromises();

    const stats = wrapper.find(".manager-stats");
    expect(stats.exists()).toBe(true);

    const panels = stats.findAll(".panel");
    expect(panels.length).toBe(6);

    const statsText = panels.map((p: DOMWrapper<Element>) => ({
      value: p.find("strong").text(),
      label: p.find("span").text(),
    }));

    expect(statsText).toEqual([
      { value: "10", label: "Назначено" },
      { value: "7", label: "Подтверждено" },
      { value: "2", label: "Отклонено" },
      { value: "1", label: "Просрочено" },
      { value: "5", label: "Срочно" },
      { value: "3", label: "Коллизии" },
    ]);
  });

  it("renders 0 for urgent and urgent_collision when summary is empty", async () => {
    const mockUser = {
      id: 1,
      username: "manager",
      full_name: "Руководитель Поддержки",
      roles: [{ id: 3, name: "Руководитель" }],
    };

    const mockManagerData = {
      summary: {
        assigned: 0,
        confirmed: 0,
        rejected: 0,
        overdue: 0,
        urgent: 0,
        urgent_collision: 0,
      },
      items: [],
      limit: 50,
    };

    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return {
          ok: true,
          status: 200,
          json: async () => mockUser,
        } as Response;
      }
      if (url.includes("/api/v1/manager/cards")) {
        return {
          ok: true,
          status: 200,
          json: async () => mockManagerData,
        } as Response;
      }
      return {
        ok: false,
        status: 404,
        json: async () => ({}),
      } as Response;
    });

    const wrapper = mount(App);
    await flushPromises();

    const stats = wrapper.find(".manager-stats");
    expect(stats.exists()).toBe(true);

    const panels = stats.findAll(".panel");
    const urgentPanel = panels.find((p: DOMWrapper<Element>) => p.find("span").text() === "Срочно");
    const collisionPanel = panels.find((p: DOMWrapper<Element>) => p.find("span").text() === "Коллизии");

    expect(urgentPanel?.find("strong").text()).toBe("0");
    expect(collisionPanel?.find("strong").text()).toBe("0");
  });
});

describe("App manager create view", () => {
  const originalFetch = globalThis.fetch;
  const mockUser = {
    id: 1,
    username: "manager",
    full_name: "Руководитель Поддержки",
    roles: [{ id: 3, name: "Руководитель" }],
  };

  function validFutureDate(hoursAhead = 3): string {
    const d = new Date(Date.now() + hoursAhead * 3600 * 1000);
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Yekaterinburg",
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(d);
    const value = (type: string) => parts.find((part) => part.type === type)?.value;
    return `${value("year")}-${value("month")}-${value("day")}T${value("hour")}:${value("minute")}`;
  }

  beforeEach(() => {
    window.history.pushState({}, "", "/manager/cards/new");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders manager create form and disables submit until preflight passes", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".create-form").exists()).toBe(true);

    const submitBtn = wrapper.find(".create-form button[type='submit']");
    expect(submitBtn.exists()).toBe(true);
    expect((submitBtn.element as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps preflight loading visible and hides server details on failure", async () => {
    let resolvePreflight: (res: Response) => void = () => {};
    const preflightResponse = new Promise<Response>((resolve) => { resolvePreflight = resolve; });
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) return { ok: true, status: 200, json: async () => mockUser } as Response;
      if (url.includes("/api/v1/manager/tickets/T-404/preflight")) return preflightResponse;
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find("input[required]").setValue("T-404");
    const preflightButton = wrapper.find(".create-form button.secondary");
    await preflightButton.trigger("click");
    expect(preflightButton.text()).toBe("Проверяем…");
    expect((preflightButton.element as HTMLButtonElement).disabled).toBe(true);
    expect((wrapper.find(".create-form button[type='submit']").element as HTMLButtonElement).disabled).toBe(true);

    resolvePreflight({ ok: false, status: 404, json: async () => ({ detail: "case_id=private" }) } as Response);
    await flushPromises();
    expect(wrapper.text()).toContain("Тикет не найден или номер не совпадает.");
    expect(wrapper.html()).not.toContain("case_id");
    expect((preflightButton.element as HTMLButtonElement).disabled).toBe(false);
  });

  it("rejects invalid create windows before sending a POST", async () => {
    const fetchSpy = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) return { ok: true, status: 200, json: async () => mockUser } as Response;
      if (url.includes("/api/v1/manager/tickets/T-VALID/preflight")) {
        return { ok: true, status: 200, json: async () => ({ case_number: "T-VALID", status: "open", client_display_name: null, can_create: true }) } as Response;
      }
      if (url.includes("/api/v1/manager/l2-options")) return { ok: true, status: 200, json: async () => ({ items: [] }) } as Response;
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    globalThis.fetch = fetchSpy;

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find("input[required]").setValue("T-VALID");
    await wrapper.find(".create-form button.secondary").trigger("click");
    await flushPromises();

    const startInput = wrapper.find("input[type='datetime-local']");
    const durationInput = wrapper.find("input[type='number']");
    const form = wrapper.find(".create-form");
    await startInput.setValue(validFutureDate(1));
    await flushPromises();
    await form.trigger("submit");
    expect(wrapper.text()).toContain("не раньше чем через 2 часа");

    await startInput.setValue(validFutureDate(15 * 24));
    await flushPromises();
    await form.trigger("submit");
    expect(wrapper.text()).toContain("не может быть дальше чем через 14 дней");

    await startInput.setValue(validFutureDate(4));
    await durationInput.setValue("20");
    await flushPromises();
    await form.trigger("submit");
    expect(wrapper.text()).toContain("от 30 до 720 минут");

    await durationInput.setValue("721");
    await flushPromises();
    await form.trigger("submit");
    expect(wrapper.text()).toContain("от 30 до 720 минут");
    expect(fetchSpy.mock.calls.some((call) => String(call[0]).includes("/api/v1/manager/cards"))).toBe(false);
  });

  it("resets stale preflight when caseNumber changes and prevents submit", async () => {
    const fetchSpy = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser } as Response;
      }
      if (url.includes("/api/v1/manager/tickets/T-1001/preflight")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ case_number: "T-1001", status: "open", client_display_name: "Иван", can_create: true }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    globalThis.fetch = fetchSpy;

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.vm.$nextTick();

    const caseInput = wrapper.find("input[required]");
    await caseInput.setValue("T-1001");

    const preflightBtn = wrapper.find("button.secondary");
    await preflightBtn.trigger("click");
    await flushPromises();
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain("Тикет T-1001");

    // Edit case number to T-1002 without preflighting T-1002
    await caseInput.setValue("T-1002");
    await flushPromises();
    await wrapper.vm.$nextTick();

    // Preflight panel for T-1001 should be hidden
    expect(wrapper.text()).not.toContain("Тикет T-1001");

    // Submit button should be disabled
    const submitBtn = wrapper.find(".create-form button[type='submit']");
    expect(submitBtn.exists()).toBe(true);
    expect((submitBtn.element as HTMLButtonElement).disabled).toBe(true);

    // Attempt form submit via submit event
    await wrapper.find(".create-form").trigger("submit");
    await flushPromises();
    await wrapper.vm.$nextTick();

    // Should NOT call POST /api/v1/manager/cards
    const postCalls = fetchSpy.mock.calls.filter((c) => String(c[0]).includes("/api/v1/manager/cards"));
    expect(postCalls.length).toBe(0);
    expect(wrapper.text()).toContain("Проверьте тикет перед созданием карточки.");
  });

  it("handles concurrent preflight requests correctly", async () => {
    let resolveT1001: (res: Response) => void = () => {};
    const t1001Promise = new Promise<Response>((r) => { resolveT1001 = r; });

    const fetchSpy = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser } as Response;
      }
      if (url.includes("/api/v1/manager/tickets/T-1001/preflight")) {
        return t1001Promise;
      }
      if (url.includes("/api/v1/manager/tickets/T-1002/preflight")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ case_number: "T-1002", status: "open", client_display_name: "Пётр", can_create: true }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    globalThis.fetch = fetchSpy;

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.vm.$nextTick();

    const caseInput = wrapper.find("input[required]");
    const preflightBtn = wrapper.find("button.secondary");

    // Request 1: T-1001 (slow)
    await caseInput.setValue("T-1001");
    await preflightBtn.trigger("click");
    await flushPromises();

    // Request 2: T-1002 (fast)
    await caseInput.setValue("T-1002");
    await preflightBtn.trigger("click");
    await flushPromises();
    await flushPromises();
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain("Тикет T-1002");

    // Now resolve slow T-1001 request
    resolveT1001({
      ok: true,
      status: 200,
      json: async () => ({ case_number: "T-1001", status: "open", client_display_name: "Иван", can_create: true }),
    } as Response);
    await flushPromises();
    await flushPromises();
    await wrapper.vm.$nextTick();

    // Active preflight must remain T-1002 and not be overwritten by T-1001
    expect(wrapper.text()).toContain("Тикет T-1002");
    expect(wrapper.text()).not.toContain("Тикет T-1001");
  });

  it("sends a manual L2 choice using the backend assignment contract", async () => {
    const fetchSpy = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) return { ok: true, status: 200, json: async () => mockUser } as Response;
      if (url.includes("/api/v1/manager/tickets/T-MANUAL/preflight")) {
        return { ok: true, status: 200, json: async () => ({ case_number: "T-MANUAL", status: "open", client_display_name: null, can_create: true }) } as Response;
      }
      if (url.includes("/api/v1/manager/l2-options")) {
        return { ok: true, status: 200, json: async () => ({ items: [{ user_id: 42, display_name: "L2 Test", available: true, reason_code: null }] }) } as Response;
      }
      if (url.includes("/api/v1/manager/cards") && init?.method === "POST") {
        return { ok: false, status: 409, json: async () => ({ detail: "l2_assignment_conflict" }) } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    globalThis.fetch = fetchSpy;

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find("input[required]").setValue("T-MANUAL");
    await wrapper.find(".create-form button.secondary").trigger("click");
    await wrapper.find("input[type='datetime-local']").setValue(validFutureDate(4));
    await flushPromises();
    await wrapper.find("input[type='radio'][value='manual']").setValue();
    await wrapper.find(".create-form select").setValue("42");
    await wrapper.find(".create-form").trigger("submit");
    await flushPromises();

    const postCalls = fetchSpy.mock.calls.filter((call) => String(call[0]).includes("/api/v1/manager/cards") && call[1]?.method === "POST");
    expect(postCalls).toHaveLength(1);
    expect(JSON.parse(String(postCalls[0][1]?.body))).toMatchObject({
      case_number: "T-MANUAL",
      assignment_method: "auto",
      l2_user_id: 42,
    });
    expect(wrapper.text()).toContain("Выбранный L2 стал недоступен.");
  });

  it("prevents double submit on rapid clicks", async () => {
    let resolvePost: (res: Response) => void = () => {};
    const postPromise = new Promise<Response>((r) => { resolvePost = r; });

    const fetchSpy = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser } as Response;
      }
      if (url.includes("/api/v1/manager/tickets/T-777/preflight")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ case_number: "T-777", status: "open", client_display_name: "Тест", can_create: true }),
        } as Response;
      }
      if (url.includes("/api/v1/manager/cards") && init?.method === "POST") {
        return postPromise;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    globalThis.fetch = fetchSpy;

    const wrapper = mount(App);
    await flushPromises();

    const caseInput = wrapper.find("input[required]");
    await caseInput.setValue("T-777");
    await wrapper.find("button.secondary").trigger("click");
    await flushPromises();

    // Fill valid start date
    const startInput = wrapper.find("input[type='datetime-local']");
    await startInput.setValue(validFutureDate(4));

    const form = wrapper.find(".create-form");

    // Rapid submit x3
    await form.trigger("submit");
    await form.trigger("submit");
    await form.trigger("submit");

    const postCalls = fetchSpy.mock.calls.filter((c) => String(c[0]).includes("/api/v1/manager/cards") && c[1]?.method === "POST");
    expect(postCalls.length).toBe(1);
    expect(JSON.parse(String(postCalls[0][1]?.body))).toMatchObject({ case_number: "T-777", assignment_method: "auto" });
    expect(String(postCalls[0][1]?.body)).not.toMatch(/case_id/i);
    expect(wrapper.html()).not.toMatch(/case_id/i);

    // Resolve POST request
    resolvePost({
      ok: true,
      status: 200,
      json: async () => ({ id: "crd_12345", number: "RD-001" }),
    } as Response);
    await flushPromises();
  });

  it("does not leak case_id in request URLs, payloads or rendered DOM", async () => {
    const fetchSpy = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser } as Response;
      }
      if (url.includes("/api/v1/manager/tickets/T-888/preflight")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ case_number: "T-888", status: "open", client_display_name: "Клиент", can_create: true }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    globalThis.fetch = fetchSpy;

    const wrapper = mount(App);
    await flushPromises();

    await wrapper.find("input[required]").setValue("T-888");
    await wrapper.find("button.secondary").trigger("click");
    await flushPromises();

    const html = wrapper.html();
    expect(html).not.toMatch(/case_id/i);

    for (const call of fetchSpy.mock.calls) {
      expect(String(call[0])).not.toMatch(/case_id/i);
      if (call[1]?.body) {
        expect(String(call[1].body)).not.toMatch(/case_id/i);
      }
    }
  });
});
