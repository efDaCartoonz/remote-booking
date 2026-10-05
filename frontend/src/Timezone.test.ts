import { flushPromises, mount } from "@vue/test-utils";
import { slotInput } from "./testSlot";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App.vue";

const originalFetch = globalThis.fetch;

const mockUser = (timezone = "Asia/Yekaterinburg", roles = [{ id: 1, name: "Специалист Л1" }]) => ({
  id: 10,
  username: "ivanov",
  full_name: "Иван Иванов",
  roles,
  timezone,
});

const mockCard = {
  id: "crd-001",
  number: "RDM-001",
  omnidesk_ticket_number: "111-222333",
  status: "assigned",
  status_label: "Назначено",
  planned_start_at: "2026-10-01T10:00:00Z",
  planned_end_at: "2026-10-01T11:00:00Z",
  planned_duration_minutes: 60,
  l1_owner_id: 10,
  l1_owner_name: "Иван Иванов",
  l2_engineer_id: 20,
  l2_engineer_name: "Петр Петров",
  client_informed: false,
  criticality_code: 0,
  urgency_code: 0,
  overdue_flag: false,
  out_of_hours_flag: false,
  retroactive_flag: false,
  description: "Тестовая карточка",
  result_code: null,
  engineer_report: null,
  actual_start_at: "2026-10-01T10:05:00Z",
  actual_end_at: "2026-10-01T10:55:00Z",
  created_at: "2026-09-30T08:00:00Z",
  updated_at: "2026-09-30T08:00:00Z",
};

const mockHistory = [
  {
    event_label: "Карточка создана",
    actor_label: "Иван Иванов",
    created_at: "2026-09-30T08:00:00Z",
  },
];

const mockNotifications = [
  {
    event: "Назначение",
    channel: "Telegram",
    status: "Отправлено",
    created_at: "2026-09-30T08:05:00Z",
    sent_at: "2026-09-30T08:05:10Z",
  },
];

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("FE-03 Profile Timezone in UI", () => {
  it("displays card planned dates, history, and notifications in saved profile timezone Asia/Yekaterinburg (UTC+5)", async () => {
    window.history.pushState({}, "", "/cards/crd-001");

    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser("Asia/Yekaterinburg") } as Response;
      }
      if (url.includes("/api/v1/cards/crd-001/history")) {
        return { ok: true, status: 200, json: async () => mockHistory } as Response;
      }
      if (url.includes("/api/v1/cards/crd-001/notifications")) {
        return { ok: true, status: 200, json: async () => ({ items: mockNotifications }) } as Response;
      }
      if (url.includes("/api/v1/cards/crd-001")) {
        return { ok: true, status: 200, json: async () => mockCard } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    const wrapper = mount(App);
    await flushPromises();

    // 2026-10-01T10:00:00Z in Asia/Yekaterinburg (UTC+5) is 15:00
    // 2026-10-01T11:00:00Z is 16:00
    expect(wrapper.text()).toContain("Asia/Yekaterinburg");
    expect(wrapper.text()).toContain("15:00");
    expect(wrapper.text()).toContain("16:00");

    // History: 2026-09-30T08:00:00Z in UTC+5 is 13:00
    expect(wrapper.find(".history").text()).toContain("13:00");

    // Notifications: 2026-09-30T08:05:00Z in UTC+5 is 13:05
    expect(wrapper.find(".notification-list").text()).toContain("13:05");
  });

  it("displays card planned dates in saved profile timezone Europe/Moscow (UTC+3) when profile is Moscow", async () => {
    window.history.pushState({}, "", "/cards/crd-001");

    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser("Europe/Moscow") } as Response;
      }
      if (url.includes("/api/v1/cards/crd-001/history")) {
        return { ok: true, status: 200, json: async () => mockHistory } as Response;
      }
      if (url.includes("/api/v1/cards/crd-001/notifications")) {
        return { ok: true, status: 200, json: async () => ({ items: mockNotifications }) } as Response;
      }
      if (url.includes("/api/v1/cards/crd-001")) {
        return { ok: true, status: 200, json: async () => mockCard } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    const wrapper = mount(App);
    await flushPromises();

    // 2026-10-01T10:00:00Z in Europe/Moscow (UTC+3) is 13:00
    expect(wrapper.text()).toContain("Europe/Moscow");
    expect(wrapper.text()).toContain("13:00");
  });

  it("allows user to update own profile timezone and updates UI immediately", async () => {
    window.history.pushState({}, "", "/cards/crd-001");

    let currentUser = mockUser("Asia/Yekaterinburg");
    const fetchSpy = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => currentUser } as Response;
      }
      if (url.includes("/api/v1/auth/timezone") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        currentUser = { ...currentUser, timezone: body.timezone };
        return { ok: true, status: 200, json: async () => ({ user_id: 10, timezone: body.timezone }) } as Response;
      }
      if (url.includes("/api/v1/cards/crd-001/history")) {
        return { ok: true, status: 200, json: async () => mockHistory } as Response;
      }
      if (url.includes("/api/v1/cards/crd-001/notifications")) {
        return { ok: true, status: 200, json: async () => ({ items: mockNotifications }) } as Response;
      }
      if (url.includes("/api/v1/cards/crd-001")) {
        return { ok: true, status: 200, json: async () => mockCard } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    globalThis.fetch = fetchSpy;

    const wrapper = mount(App);
    await flushPromises();

    // Initially Asia/Yekaterinburg (15:00)
    expect(wrapper.text()).toContain("Asia/Yekaterinburg");
    expect(wrapper.text()).toContain("15:00");

    // The card page no longer carries the timezone form: it lives in the profile page.
    expect(wrapper.find(".profile-tz-panel").exists()).toBe(false);
    wrapper.unmount();

    // Change profile timezone to UTC on the profile page
    window.history.pushState({}, "", "/profile");
    const profile = mount(App);
    await flushPromises();
    const tzSelect = profile.find(".profile-tz-panel select");
    expect(tzSelect.exists()).toBe(true);
    const labels = tzSelect.findAll("option").map((option) => option.text());
    expect(labels.length).toBeGreaterThan(10);
    expect(labels.some((label) => label.includes("Екатеринбург (UTC+05:00)"))).toBe(true);
    expect(labels.some((label) => label.includes("Москва (UTC+03:00)"))).toBe(true);
    await tzSelect.setValue("UTC");

    const tzForm = profile.find(".profile-tz-panel form");
    await tzForm.trigger("submit");
    await flushPromises();

    // Verify PUT /api/v1/auth/timezone called
    const putCalls = fetchSpy.mock.calls.filter(
      (c) => String(c[0]).includes("/api/v1/auth/timezone") && c[1]?.method === "PUT"
    );
    expect(putCalls.length).toBe(1);
    expect(JSON.parse(String(putCalls[0][1]?.body))).toEqual({ timezone: "UTC" });

    // Success notice is displayed on the profile page
    expect(profile.text()).toContain("Часовой пояс сохранён.");
  });

  it("manager date filter converts day bounds according to profile timezone", async () => {
    window.history.pushState({}, "", "/manager");

    const managerData = {
      summary: { assigned: 1, confirmed: 0, rejected: 0, overdue: 0, urgent: 0, urgent_collision: 0 },
      items: [
        {
          public_id: "m-001",
          number: "RDM-M1",
          omnidesk_ticket_number: "999-000111",
          status: "assigned",
          status_label: "Назначено",
          planned_start_at: "2026-10-01T10:00:00Z",
          planned_end_at: "2026-10-01T11:00:00Z",
          planned_duration_minutes: 60,
          l1_owner_name: null,
          l2_engineer_name: null,
          urgent: false,
          overdue: false,
          out_of_hours: false,
          first_unsuccessful_cycle: false,
          repeated_unsuccessful_cycle: false,
        },
      ],
      limit: 50,
    };

    const fetchSpy = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser("Asia/Yekaterinburg", [{ id: 3, name: "Руководитель" }]) } as Response;
      }
      if (url.includes("/api/v1/manager/cards")) {
        return { ok: true, status: 200, json: async () => managerData } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    globalThis.fetch = fetchSpy;

    const wrapper = mount(App);
    await flushPromises();

    // Fill date filter: from 2026-10-01 to 2026-10-01
    const dateInputs = wrapper.findAll(".manager-filters input[type='date']");
    expect(dateInputs.length).toBe(2);
    await dateInputs[0].setValue("2026-10-01");
    await dateInputs[1].setValue("2026-10-01");

    await wrapper.find(".manager-filters").trigger("submit");
    await flushPromises();

    // In Asia/Yekaterinburg (UTC+5):
    // 2026-10-01 00:00:00 is 2026-09-30T19:00:00.000Z
    // 2026-10-02 00:00:00 is 2026-10-01T19:00:00.000Z
    const managerCalls = fetchSpy.mock.calls.filter((c) => String(c[0]).includes("/api/v1/manager/cards?"));
    const lastCallUrl = String(managerCalls[managerCalls.length - 1][0]);

    expect(lastCallUrl).toContain("period_from=2026-09-30T19%3A00%3A00.000Z");
    expect(lastCallUrl).toContain("period_to=2026-10-01T19%3A00%3A00.000Z");
  });

  it("interprets manager creation time in the saved profile timezone", async () => {
    window.history.pushState({}, "", "/manager/cards/new");
    const start = new Date(Math.ceil((Date.now() + 4 * 3600_000) / 60_000) * 60_000);
    const localStart = new Intl.DateTimeFormat("sv-SE", {
      timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(start).replace(" ", "T");
    const fetchSpy = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) return { ok: true, status: 200, json: async () => mockUser("Europe/Moscow", [{ id: 3, name: "Руководитель" }]) } as Response;
      if (url.includes("/api/v1/manager/tickets/T-TZ/preflight")) return { ok: true, status: 200, json: async () => ({ case_number: "T-TZ", status: "open", client_display_name: null, can_create: true }) } as Response;
      if (url.includes("/api/v1/manager/l2-options")) return { ok: true, status: 200, json: async () => ({ items: [] }) } as Response;
      if (url.includes("/api/v1/manager/cards") && init?.method === "POST") return { ok: false, status: 409, json: async () => ({ detail: "active_card_exists_for_ticket" }) } as Response;
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    globalThis.fetch = fetchSpy;

    const wrapper = mount(App);
    await flushPromises();
    await wrapper.find(".create-form input[required]").setValue("T-TZ");
    await wrapper.find(".create-form button.secondary").trigger("click");
    await flushPromises();
    await slotInput(wrapper.find(".create-form")).setValue(localStart);
    await flushPromises();
    await wrapper.find(".create-form").trigger("submit");
    await flushPromises();

    const post = fetchSpy.mock.calls.find((call) => String(call[0]).includes("/api/v1/manager/cards") && call[1]?.method === "POST");
    expect(post).toBeDefined();
    expect(JSON.parse(String(post?.[1]?.body)).planned_start_at).toBe(start.toISOString());
  });

  it("falls back safely to list with explicit notice on spring DST transition in a DST zone (Europe/London)", async () => {
    window.history.pushState({}, "", "/manager");

    const managerData = {
      summary: { assigned: 1, confirmed: 0, rejected: 0, overdue: 0, urgent: 0, urgent_collision: 0 },
      items: [
        {
          public_id: "m-dst-1",
          number: "RDM-SPRING",
          omnidesk_ticket_number: "100-000001",
          status: "assigned",
          status_label: "Назначено",
          planned_start_at: "2026-03-29T10:00:00Z",
          planned_end_at: "2026-03-29T11:00:00Z",
          planned_duration_minutes: 60,
          l1_owner_name: null,
          l2_engineer_name: "John",
          urgent: false,
          overdue: false,
          out_of_hours: false,
          first_unsuccessful_cycle: false,
          repeated_unsuccessful_cycle: false,
        },
      ],
      limit: 50,
    };

    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser("Europe/London", [{ id: 3, name: "Руководитель" }]) } as Response;
      }
      if (url.includes("/api/v1/manager/cards")) {
        return { ok: true, status: 200, json: async () => managerData } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    const wrapper = mount(App);
    await flushPromises();

    // Filter to the spring DST transition day: 2026-03-29
    const dateInputs = wrapper.findAll(".manager-filters input[type='date']");
    await dateInputs[0].setValue("2026-03-29");
    await dateInputs[1].setValue("2026-03-29");
    await wrapper.find(".manager-filters").trigger("submit");
    await flushPromises();

    // Toggle to calendar view
    const viewButtons = wrapper.findAll(".manager-toggle button");
    const calendarBtn = viewButtons.find((b) => b.text() === "Календарь");
    await calendarBtn?.trigger("click");
    await flushPromises();

    // In Europe/London on 2026-03-29, clocks skip 01:00->02:00 (23h day).
    // Calendar should show fallback warning and list instead of corrupted grid
    expect(wrapper.find(".dst-notice").exists()).toBe(true);
    expect(wrapper.text()).toContain("переход на сезонное время (DST)");
    expect(wrapper.find(".calendar").exists()).toBe(false);
    expect(wrapper.text()).toContain("RDM-SPRING");
  });

  it("falls back safely to list with explicit notice on fall DST transition in a DST zone (Europe/London)", async () => {
    window.history.pushState({}, "", "/manager");

    const managerData = {
      summary: { assigned: 1, confirmed: 0, rejected: 0, overdue: 0, urgent: 0, urgent_collision: 0 },
      items: [
        {
          public_id: "m-dst-2",
          number: "RDM-FALL",
          omnidesk_ticket_number: "100-000002",
          status: "confirmed",
          status_label: "Подтверждено",
          planned_start_at: "2026-10-25T01:30:00Z",
          planned_end_at: "2026-10-25T02:30:00Z",
          planned_duration_minutes: 60,
          l1_owner_name: null,
          l2_engineer_name: "Alice",
          urgent: false,
          overdue: false,
          out_of_hours: false,
          first_unsuccessful_cycle: false,
          repeated_unsuccessful_cycle: false,
        },
      ],
      limit: 50,
    };

    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser("Europe/London", [{ id: 3, name: "Руководитель" }]) } as Response;
      }
      if (url.includes("/api/v1/manager/cards")) {
        return { ok: true, status: 200, json: async () => managerData } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    const wrapper = mount(App);
    await flushPromises();

    // Filter to fall DST transition day: 2026-10-25
    const dateInputs = wrapper.findAll(".manager-filters input[type='date']");
    await dateInputs[0].setValue("2026-10-25");
    await dateInputs[1].setValue("2026-10-25");
    await wrapper.find(".manager-filters").trigger("submit");
    await flushPromises();

    // Toggle to calendar view
    const viewButtons = wrapper.findAll(".manager-toggle button");
    const calendarBtn = viewButtons.find((b) => b.text() === "Календарь");
    await calendarBtn?.trigger("click");
    await flushPromises();

    // In Europe/London on 2026-10-25, clocks repeat 01:00->02:00 (25h day).
    expect(wrapper.find(".dst-notice").exists()).toBe(true);
    expect(wrapper.find(".calendar").exists()).toBe(false);
    expect(wrapper.text()).toContain("RDM-FALL");
  });

  it("renders standard calendar grid without DST notice for non-DST zones (Asia/Yekaterinburg, Europe/Moscow)", async () => {
    window.history.pushState({}, "", "/manager");

    const managerData = {
      summary: { assigned: 1, confirmed: 0, rejected: 0, overdue: 0, urgent: 0, urgent_collision: 0 },
      items: [
        {
          public_id: "m-std-1",
          number: "RDM-STD1",
          omnidesk_ticket_number: "222-000001",
          status: "assigned",
          status_label: "Назначено",
          planned_start_at: "2026-03-29T10:00:00Z",
          planned_end_at: "2026-03-29T11:00:00Z",
          planned_duration_minutes: 60,
          l1_owner_name: null,
          l2_engineer_name: "Engineer",
          urgent: false,
          overdue: false,
          out_of_hours: false,
          first_unsuccessful_cycle: false,
          repeated_unsuccessful_cycle: false,
        },
      ],
      limit: 50,
    };

    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser("Asia/Yekaterinburg", [{ id: 3, name: "Руководитель" }]) } as Response;
      }
      if (url.includes("/api/v1/manager/cards")) {
        return { ok: true, status: 200, json: async () => managerData } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    const wrapper = mount(App);
    await flushPromises();

    const dateInputs = wrapper.findAll(".manager-filters input[type='date']");
    await dateInputs[0].setValue("2026-03-29");
    await dateInputs[1].setValue("2026-03-29");
    await wrapper.find(".manager-filters").trigger("submit");
    await flushPromises();

    // Toggle to calendar view
    const viewButtons = wrapper.findAll(".manager-toggle button");
    const calendarBtn = viewButtons.find((b) => b.text() === "Календарь");
    await calendarBtn?.trigger("click");
    await flushPromises();

    // Non-DST zone should render .calendar grid cleanly and not show DST notice
    expect(wrapper.find(".dst-notice").exists()).toBe(false);
    expect(wrapper.find(".calendar").exists()).toBe(true);
    expect(wrapper.findAll(".calendar-column").length).toBe(7);

    // Switch to day mode
    const dayBtn = wrapper.findAll(".manager-toggle button").find((b) => b.text() === "День");
    await dayBtn?.trigger("click");
    await flushPromises();

    expect(wrapper.find(".dst-notice").exists()).toBe(false);
    expect(wrapper.find(".calendar").exists()).toBe(true);
    expect(wrapper.findAll(".calendar-column").length).toBe(1);

    // Event style check: 10:00Z in Asia/Yekaterinburg (UTC+5) is 15:00 local time
    // 15 hours * 80px/hour = 1200px
    const event = wrapper.find(".calendar-event");
    expect(event.exists()).toBe(true);
    expect(event.attributes("style")).toContain("top: 1200px");
    expect(event.attributes("style")).toContain("height: 80px");
  });

  it("renders standard calendar grid in DST zone on normal non-transition week", async () => {
    window.history.pushState({}, "", "/manager");

    const managerData = {
      summary: { assigned: 1, confirmed: 0, rejected: 0, overdue: 0, urgent: 0, urgent_collision: 0 },
      items: [
        {
          public_id: "m-summer-1",
          number: "RDM-SUMMER",
          omnidesk_ticket_number: "333-000001",
          status: "assigned",
          status_label: "Назначено",
          planned_start_at: "2026-07-06T09:00:00Z",
          planned_end_at: "2026-07-06T10:00:00Z",
          planned_duration_minutes: 60,
          l1_owner_name: null,
          l2_engineer_name: "Engineer",
          urgent: false,
          overdue: false,
          out_of_hours: false,
          first_unsuccessful_cycle: false,
          repeated_unsuccessful_cycle: false,
        },
      ],
      limit: 50,
    };

    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser("Europe/London", [{ id: 3, name: "Руководитель" }]) } as Response;
      }
      if (url.includes("/api/v1/manager/cards")) {
        return { ok: true, status: 200, json: async () => managerData } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    const wrapper = mount(App);
    await flushPromises();

    const dateInputs = wrapper.findAll(".manager-filters input[type='date']");
    await dateInputs[0].setValue("2026-07-06");
    await dateInputs[1].setValue("2026-07-12");
    await wrapper.find(".manager-filters").trigger("submit");
    await flushPromises();

    const calendarBtn = wrapper.findAll(".manager-toggle button").find((b) => b.text() === "Календарь");
    await calendarBtn?.trigger("click");
    await flushPromises();

    // Summer week in London has no transitions -> normal calendar grid
    expect(wrapper.find(".dst-notice").exists()).toBe(false);
    expect(wrapper.find(".calendar").exists()).toBe(true);
    expect(wrapper.findAll(".calendar-column").length).toBe(7);
  });

  it("strictly preserves local midnight boundaries without off-by-one errors across day shifts", async () => {
    window.history.pushState({}, "", "/manager");

    // 2026-10-01 23:30 local in Asia/Yekaterinburg (UTC+5) = 2026-10-01T18:30:00Z
    // 2026-10-02 00:30 local in Asia/Yekaterinburg (UTC+5) = 2026-10-01T19:30:00Z
    const managerData = {
      summary: { assigned: 2, confirmed: 0, rejected: 0, overdue: 0, urgent: 0, urgent_collision: 0 },
      items: [
        {
          public_id: "m-b1",
          number: "RDM-DAY1",
          omnidesk_ticket_number: "444-000001",
          status: "assigned",
          status_label: "Назначено",
          planned_start_at: "2026-10-01T18:30:00Z",
          planned_end_at: "2026-10-01T19:00:00Z",
          planned_duration_minutes: 30,
          l1_owner_name: null,
          l2_engineer_name: "Engineer",
          urgent: false,
          overdue: false,
          out_of_hours: false,
          first_unsuccessful_cycle: false,
          repeated_unsuccessful_cycle: false,
        },
        {
          public_id: "m-b2",
          number: "RDM-DAY2",
          omnidesk_ticket_number: "444-000002",
          status: "assigned",
          status_label: "Назначено",
          planned_start_at: "2026-10-01T19:30:00Z",
          planned_end_at: "2026-10-01T20:00:00Z",
          planned_duration_minutes: 30,
          l1_owner_name: null,
          l2_engineer_name: "Engineer",
          urgent: false,
          overdue: false,
          out_of_hours: false,
          first_unsuccessful_cycle: false,
          repeated_unsuccessful_cycle: false,
        },
      ],
      limit: 50,
    };

    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) {
        return { ok: true, status: 200, json: async () => mockUser("Asia/Yekaterinburg", [{ id: 3, name: "Руководитель" }]) } as Response;
      }
      if (url.includes("/api/v1/manager/cards")) {
        return { ok: true, status: 200, json: async () => managerData } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    const wrapper = mount(App);
    await flushPromises();

    // Filter day mode for 2026-10-01
    const dateInputs = wrapper.findAll(".manager-filters input[type='date']");
    await dateInputs[0].setValue("2026-10-01");
    await dateInputs[1].setValue("2026-10-01");
    await wrapper.find(".manager-filters").trigger("submit");
    await flushPromises();

    await wrapper.findAll(".manager-toggle button").find((b) => b.text() === "Календарь")?.trigger("click");
    await flushPromises();
    await wrapper.findAll(".manager-toggle button").find((b) => b.text() === "День")?.trigger("click");
    await flushPromises();

    // Day 2026-10-01 in Yekaterinburg should include RDM-DAY1 (23:30) and exclude RDM-DAY2 (which is 00:30 on 2026-10-02)
    const column = wrapper.find(".calendar-column");
    expect(column.text()).toContain("RDM-DAY1");
    expect(column.text()).not.toContain("RDM-DAY2");

    // Position of RDM-DAY1: 23:30 local -> 23.5 * 80px = 1880px
    const event1 = column.find(".calendar-event");
    expect(event1.attributes("style")).toContain("top: 1880px");
    expect(event1.attributes("style")).toContain("height: 40px");
  });
});
