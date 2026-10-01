import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CancelApp from "./CancelApp.vue";

const originalFetch = globalThis.fetch;

function getMockVerification(overrides = {}) {
  return {
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
    ...overrides,
  };
}

describe("CancelApp component", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let replaceStateSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as any;
    replaceStateSpy = vi.spyOn(window.history, "replaceState");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    window.location.hash = "";
    vi.restoreAllMocks();
  });

  it("shows error if no token is found in the URL fragment", async () => {
    window.location.hash = "";
    const wrapper = mount(CancelApp);
    await flushPromises();

    expect(wrapper.text()).toContain("Отсутствует токен отмены");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("extracts token from fragment, immediately strips it from URL, and verifies via POST", async () => {
    const secretToken = "secret-cancellation-token-xyz-123";
    window.location.hash = `#token=${secretToken}`;

    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/v1/cancellation/verify") {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({ token: secretToken });
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => getMockVerification(),
        });
      }
      return Promise.reject(new Error("Unknown url: " + url));
    });

    const wrapper = mount(CancelApp);
    await flushPromises();

    // Verify fragment was stripped from URL immediately
    expect(replaceStateSpy).toHaveBeenCalled();

    // Verify token does NOT leak in DOM text or HTML attributes
    expect(wrapper.html()).not.toContain(secretToken);
    expect(wrapper.text()).not.toContain(secretToken);

    // Verify safe card details are rendered
    expect(wrapper.text()).toContain("Обращение #123-456789");
    expect(wrapper.text()).toContain("Назначено");
    expect(wrapper.text()).toContain("60 мин");

    // Verify deliberate confirmation button exists
    const confirmBtn = wrapper.find("button.cancel-btn-confirm");
    expect(confirmBtn.exists()).toBe(true);
    expect(confirmBtn.text()).toContain("Подтвердить отмену записи");
  });

  it("supports raw token in fragment (#<opaque_token>) and verifies cleanly", async () => {
    const rawToken = "raw-opaque-token-999";
    window.location.hash = `#${rawToken}`;

    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/v1/cancellation/verify") {
        expect(JSON.parse(String(init?.body))).toEqual({ token: rawToken });
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => getMockVerification(),
        });
      }
      return Promise.reject(new Error("Unknown url"));
    });

    const wrapper = mount(CancelApp);
    await flushPromises();

    expect(wrapper.text()).toContain("Обращение #123-456789");
    expect(wrapper.html()).not.toContain(rawToken);
  });

  it("requires deliberate confirmation click, sends token to confirm endpoint, and prevents duplicate submits", async () => {
    const token = "confirm-token-abc";
    window.location.hash = `#token=${token}`;

    let confirmResolve: (val: any) => void;
    const confirmPromise = new Promise((resolve) => {
      confirmResolve = resolve;
    });

    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/v1/cancellation/verify") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => getMockVerification(),
        });
      }
      if (url === "/api/v1/cancellation/confirm") {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({ token });
        return confirmPromise;
      }
      return Promise.reject(new Error("Unknown url"));
    });

    const wrapper = mount(CancelApp);
    await flushPromises();

    const confirmBtn = wrapper.find("button.cancel-btn-confirm");
    expect(confirmBtn.attributes("disabled")).toBeUndefined();

    // Click confirmation button
    await confirmBtn.trigger("click");

    // Button becomes disabled during submission
    expect(confirmBtn.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("Отмена записи...");

    // Resolve confirm request
    confirmResolve!({
      ok: true,
      status: 200,
      json: async () => ({
        status: "cancelled",
        card_public_id: "00000000-0000-0000-0000-000000000001",
        cancelled_at: "2026-10-15T09:02:00Z",
        idempotent: false,
      }),
    });
    await flushPromises();

    // Success screen rendered
    expect(wrapper.text()).toContain("Запись успешно отменена");
    expect(wrapper.text()).toContain("Обращение #123-456789");
    expect(wrapper.html()).not.toContain(token);
  });

  it("handles idempotent confirm response with clear notification", async () => {
    const token = "idempotent-token-123";
    window.location.hash = `#token=${token}`;

    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/v1/cancellation/verify") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => getMockVerification(),
        });
      }
      if (url === "/api/v1/cancellation/confirm") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            status: "cancelled",
            card_public_id: "00000000-0000-0000-0000-000000000001",
            cancelled_at: "2026-10-15T09:00:00Z",
            idempotent: true,
          }),
        });
      }
      return Promise.reject(new Error("Unknown url"));
    });

    const wrapper = mount(CancelApp);
    await flushPromises();

    await wrapper.find("button.cancel-btn-confirm").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Запись успешно отменена");
    expect(wrapper.text()).toContain("Запись уже была отменена ранее");
  });

  it("handles expired token on verify (410 Gone / cancellation_token_expired)", async () => {
    window.location.hash = "#token=expired-token-123";

    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/v1/cancellation/verify") {
        return Promise.resolve({
          ok: false,
          status: 410,
          json: async () => ({ detail: "cancellation_token_expired" }),
        });
      }
      return Promise.reject(new Error("Unknown url"));
    });

    const wrapper = mount(CancelApp);
    await flushPromises();

    expect(wrapper.find(".cancel-banner-error").exists()).toBe(true);
    expect(wrapper.text()).toContain("Срок действия ссылки отмены истёк");
    expect(wrapper.html()).not.toContain("expired-token-123");
  });

  it("handles 404 not found on verify without leaking token", async () => {
    window.location.hash = "#token=nonexistent-token";

    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/v1/cancellation/verify") {
        return Promise.resolve({
          ok: false,
          status: 404,
          json: async () => ({ detail: "cancellation_not_found" }),
        });
      }
      return Promise.reject(new Error("Unknown url"));
    });

    const wrapper = mount(CancelApp);
    await flushPromises();

    expect(wrapper.text()).toContain("Ссылка отмены не найдена или уже была использована");
    expect(wrapper.html()).not.toContain("nonexistent-token");
  });

  it("handles can_cancel = false for already cancelled card", async () => {
    window.location.hash = "#token=already-cancelled-token";

    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/v1/cancellation/verify") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () =>
            getMockVerification({
              card_status: "cancelled",
              card_status_label: "Отменено",
              can_cancel: false,
            }),
        });
      }
      return Promise.reject(new Error("Unknown url"));
    });

    const wrapper = mount(CancelApp);
    await flushPromises();

    expect(wrapper.text()).toContain("Данная запись уже была отменена ранее");
    expect(wrapper.find("button.cancel-btn-confirm").exists()).toBe(false);
  });

  it("handles confirm error (409 conflict) cleanly", async () => {
    window.location.hash = "#token=conflict-token";

    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/v1/cancellation/verify") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => getMockVerification(),
        });
      }
      if (url === "/api/v1/cancellation/confirm") {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: async () => ({ detail: "cancellation_conflict" }),
        });
      }
      return Promise.reject(new Error("Unknown url"));
    });

    const wrapper = mount(CancelApp);
    await flushPromises();

    await wrapper.find("button.cancel-btn-confirm").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Запись уже была отменена или не может быть отменена");
    expect(wrapper.html()).not.toContain("conflict-token");
  });
});
