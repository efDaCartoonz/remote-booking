import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FrameApp from "./FrameApp.vue";

const originalFetch = globalThis.fetch;

const mockCardsData = {
  omnidesk_ticket_number: "123-456789",
  can_create: true,
  cards: [
    {
      id: "00000000-0000-0000-0000-000000000001",
      status: "assigned",
      status_label: "Назначено",
      planned_start_at: "2026-10-15T09:00:00Z",
      planned_end_at: "2026-10-15T10:00:00Z",
      planned_duration_minutes: 60,
      client_timezone_at_creation: "Europe/Moscow",
      description: "Первичная диагностика подключения",
      available_actions: ["read", "request_cancellation_link"],
    },
  ],
  client_name: "Иван Клиент",
  client_company_name: "ООО Ромашка",
  client_contact_value: "client@example.test",
};

describe("FrameApp component", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as any;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows waiting state until ticket context is received", async () => {
    const wrapper = mount(FrameApp);
    expect(wrapper.text()).toContain("Ожидание параметров обращения");
    expect(wrapper.find(".frame-create-section").exists()).toBe(false);
  });

  it("ignores frame context messages from an untrusted origin or source", async () => {
    const wrapper = mount(FrameApp);
    const payload = { type: "RDM_FRAME_INIT", payload: { omnidesk_ticket_number: "123-456789", case_id: "2000" } };
    window.dispatchEvent(new MessageEvent("message", { origin: "https://attacker.invalid", source: window, data: payload }));
    window.dispatchEvent(new MessageEvent("message", { origin: "https://iridi.omnidesk.ru", data: payload }));
    await flushPromises();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("Ожидание параметров обращения");
  });

  it("handles RDM_FRAME_INIT postMessage, creates session and fetches cards", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/v1/frame/sessions") {
        return Promise.resolve({
          ok: true,
          status: 201,
          json: async () => ({
            token: "test-frame-token-xyz",
            omnidesk_ticket_number: "123-456789",
            expires_at: "2026-10-15T12:00:00Z",
            permissions: ["cards:read", "cards:create"],
          }),
        });
      }
      if (url === "/api/v1/frame/cards") {
        expect(init?.headers).toMatchObject({
          "x-rdm-frame-token": "test-frame-token-xyz",
        });
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => mockCardsData,
        });
      }
      return Promise.reject(new Error("Unknown url: " + url));
    });

    const wrapper = mount(FrameApp);

    // Simulate postMessage from parent Omnidesk frame
    window.dispatchEvent(
      new MessageEvent("message", {
        origin: "https://iridi.omnidesk.ru",
        source: window,
        data: {
          type: "RDM_FRAME_INIT",
          payload: {
            omnidesk_ticket_number: "123-456789",
            case_id: "2000",
          },
        },
      })
    );

    await flushPromises();

    // Verify session creation called with case_id in body
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/frame/sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          omnidesk_ticket_number: "123-456789",
          case_id: "2000",
        }),
      })
    );

    // Verify ticket number and cards displayed
    expect(wrapper.text()).toContain("Обращение #123-456789");
    expect(wrapper.text()).toContain("Назначено");
    expect(wrapper.text()).toContain("60 мин");
    expect(wrapper.text()).toContain("Первичная диагностика подключения");

    // Verify client-safe reschedule instruction is present
    expect(wrapper.text()).toContain("напишите сообщение в переписке по обращению");

    // Verify cancellation link request button exists for assigned card
    const cancelLinkBtn = wrapper.find("button.frame-btn-cancel-link");
    expect(cancelLinkBtn.exists()).toBe(true);
    expect(cancelLinkBtn.text()).toContain("Запросить ссылку для отмены");

    // Verify NO direct cancel or reschedule action buttons exist
    const buttons = wrapper.findAll("button");
    const buttonTexts = buttons.map((b) => b.text().toLowerCase());
    for (const text of buttonTexts) {
      expect(text).not.toContain("перенести");
      expect(text).not.toEqual("отменить");
    }

    // Verify case_id is NOT leaked anywhere in rendered HTML
    expect(wrapper.html()).not.toContain("2000");
  });

  it("requests cancellation link for card with x-rdm-frame-token and displays 5 min hint", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/v1/frame/sessions") {
        return Promise.resolve({
          ok: true,
          status: 201,
          json: async () => ({
            token: "frame-tok-abc",
            omnidesk_ticket_number: "123-456789",
            expires_at: "2026-10-15T12:00:00Z",
            permissions: ["cards:read", "cards:create"],
          }),
        });
      }
      if (url === "/api/v1/frame/cards") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => mockCardsData,
        });
      }
      if (url === "/api/v1/frame/cards/00000000-0000-0000-0000-000000000001/cancellation-link") {
        expect(init?.method).toBe("POST");
        expect(init?.headers).toMatchObject({
          "x-rdm-frame-token": "frame-tok-abc",
        });
        expect(init?.body).toBeUndefined();
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            status: "link_queued",
            expires_in_seconds: 300,
          }),
        });
      }
      return Promise.reject(new Error("Unknown url: " + url));
    });

    const wrapper = mount(FrameApp, {
      props: {
        initialTicketNumber: "123-456789",
        initialCaseId: "2000",
      },
    });

    await flushPromises();

    // Verify hint about ticket link and 5 min validity
    expect(wrapper.text()).toContain("Она действует 5 минут с момента запроса");

    // Click request link button
    const cancelBtn = wrapper.find("button.frame-btn-cancel-link");
    await cancelBtn.trigger("click");
    await flushPromises();

    // Verify success banner explaining link sent through ticket and works for 5 min
    expect(wrapper.text()).toContain("Запрос принят. Ссылка появится в переписке по обращению");
    expect(wrapper.text()).toContain("до истечения 5 минут");
  });

  it("handles cancellation link request throttling (429) gracefully", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/v1/frame/sessions") {
        return Promise.resolve({
          ok: true,
          status: 201,
          json: async () => ({ token: "frame-tok", omnidesk_ticket_number: "123-456789" }),
        });
      }
      if (url === "/api/v1/frame/cards") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => mockCardsData,
        });
      }
      if (url.includes("/cancellation-link")) {
        return Promise.resolve({
          ok: false,
          status: 429,
          json: async () => ({ detail: "cancellation_link_throttled" }),
        });
      }
      return Promise.reject(new Error("Unknown url: " + url));
    });

    const wrapper = mount(FrameApp, {
      props: {
        initialTicketNumber: "123-456789",
        initialCaseId: "2000",
      },
    });

    await flushPromises();

    await wrapper.find("button.frame-btn-cancel-link").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Ссылка для отмены недавно запрашивалась");
  });

  it("prefills UI-017 client_name, client_company_name, and contact, and submits all fields", async () => {
    let cardCreatedBody: any = null;

    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/v1/frame/sessions") {
        return Promise.resolve({
          ok: true,
          status: 201,
          json: async () => ({
            token: "tok-123",
            omnidesk_ticket_number: "123-456789",
            expires_at: "2026-10-15T12:00:00Z",
            permissions: ["cards:read", "cards:create"],
          }),
        });
      }
      if (url === "/api/v1/frame/cards" && (!init || init.method === undefined || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            ...mockCardsData,
            can_create: true,
          }),
        });
      }
      if (url === "/api/v1/frame/cards" && init?.method === "POST") {
        cardCreatedBody = JSON.parse(String(init.body));
        return Promise.resolve({
          ok: true,
          status: 201,
          json: async () => ({
            id: "new-card-uuid",
            status: "rejected",
            status_label: "Отклонено",
            planned_start_at: cardCreatedBody.planned_start_at,
            planned_end_at: cardCreatedBody.planned_start_at,
            planned_duration_minutes: 60,
            client_timezone_at_creation: cardCreatedBody.client_timezone_at_creation,
            description: cardCreatedBody.description,
            available_actions: ["read", "request_cancellation_link"],
          }),
        });
      }
      return Promise.reject(new Error("Unknown url: " + url));
    });

    const wrapper = mount(FrameApp, {
      props: {
        initialTicketNumber: "123-456789",
        initialCaseId: "2000",
      },
    });

    await flushPromises();

    // Verify UI-017 prefilled client name, company name, contact
    const nameInput = wrapper.find<HTMLInputElement>("input[placeholder='Имя клиента']");
    const companyInput = wrapper.find<HTMLInputElement>("input[placeholder='Название компании']");
    const contactInput = wrapper.find<HTMLInputElement>("input[placeholder='Email или телефон']");

    expect(nameInput.element.value).toBe("Иван Клиент");
    expect(companyInput.element.value).toBe("ООО Ромашка");
    expect(contactInput.element.value).toBe("client@example.test");

    // Edit client name and company name
    await nameInput.setValue("Петр Клиент");
    await companyInput.setValue("АО Технологии");

    // Fill creation form date/time
    await wrapper.find<HTMLInputElement>("input[type='date']").setValue("2026-10-20");
    await wrapper.find<HTMLInputElement>("input[type='time']").setValue("15:00");
    await wrapper.find<HTMLTextAreaElement>("textarea").setValue("Тестовое описание подключения");

    // Submit form
    await wrapper.find("form").trigger("submit.prevent");
    await flushPromises();

    expect(cardCreatedBody).not.toBeNull();
    expect(cardCreatedBody.client_name).toBe("Петр Клиент");
    expect(cardCreatedBody.client_company_name).toBe("АО Технологии");
    expect(cardCreatedBody.client_contact_value).toBe("client@example.test");
    expect(cardCreatedBody.planned_duration_minutes).toBe(60);
    expect(cardCreatedBody.description).toBe("Тестовое описание подключения");

    // Success message
    expect(wrapper.text()).toContain("Запись успешно оформлена!");
  });

  it("switches timezone and updates card time format and timezone_source_code", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/v1/frame/sessions") {
        return Promise.resolve({
          ok: true,
          status: 201,
          json: async () => ({
            token: "test-token",
            omnidesk_ticket_number: "123-456789",
            expires_at: "2026-10-15T12:00:00Z",
            permissions: ["cards:read", "cards:create"],
          }),
        });
      }
      if (url === "/api/v1/frame/cards") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => mockCardsData,
        });
      }
      return Promise.reject(new Error("Unknown url"));
    });

    const wrapper = mount(FrameApp, {
      props: {
        initialTicketNumber: "123-456789",
        initialCaseId: "2000",
      },
    });

    await flushPromises();

    expect(wrapper.text()).toContain("определен автоматически");

    // Change timezone in dropdown to Asia/Yekaterinburg (UTC+5)
    const select = wrapper.find<HTMLInputElement>("#tz-select");
    await select.setValue("Asia/Yekaterinburg");
    await select.trigger("change");
    await flushPromises();

    expect(wrapper.text()).toContain("выбран вручную");
    expect(wrapper.text()).toContain("14:00");
  });

  it("hides create form when can_create is false and shows active record notice", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/v1/frame/sessions") {
        return Promise.resolve({
          ok: true,
          status: 201,
          json: async () => ({
            token: "tok-123",
            omnidesk_ticket_number: "123-456789",
            expires_at: "2026-10-15T12:00:00Z",
            permissions: ["cards:read"],
          }),
        });
      }
      if (url === "/api/v1/frame/cards") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            ...mockCardsData,
            can_create: false,
          }),
        });
      }
      return Promise.reject(new Error("Unknown url"));
    });

    const wrapper = mount(FrameApp, {
      props: {
        initialTicketNumber: "123-456789",
        initialCaseId: "2000",
      },
    });

    await flushPromises();

    expect(wrapper.find(".frame-create-section").exists()).toBe(false);
    expect(wrapper.text()).toContain("По данному обращению уже оформлена активная запись");
  });

  it("displays clear error message when backend returns error", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/v1/frame/sessions") {
        return Promise.resolve({
          ok: false,
          status: 403,
          json: async () => ({ detail: "ticket_client_mismatch" }),
        });
      }
      return Promise.reject(new Error("Unknown url"));
    });

    const wrapper = mount(FrameApp, {
      props: {
        initialTicketNumber: "123-456789",
        initialCaseId: "9999",
      },
    });

    await flushPromises();

    expect(wrapper.find(".frame-banner-error").exists()).toBe(true);
    expect(wrapper.text()).toContain("Доступ ограничен: обращение привязано к другому пользователю.");
    expect(wrapper.html()).not.toContain("9999");
  });
});
