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
