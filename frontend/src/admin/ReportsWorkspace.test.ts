import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReportsWorkspace from "./ReportsWorkspace.vue";

const originalFetch = globalThis.fetch;

const ok = (body: unknown): Response =>
  ({
    ok: true,
    status: 200,
    json: async () => body,
  } as Response);

const fail = (status: number, detail: unknown): Response =>
  ({
    ok: false,
    status,
    json: async () => ({ detail }),
  } as Response);

const mockSummary = {
  created: 12,
  completed: 8,
  rejected_share: {
    numerator: 2,
    denominator: 12,
    value: 0.1666666667,
  },
  repeat_rejected_share: {
    numerator: 0,
    denominator: 0,
    value: null, // Null value! Must show "нет данных", NOT "0%"
  },
  overdue: { count: 3 },
  urgent: { count: 2 },
  urgent_collisions: { count: 1 },
};

const mockOverdue = {
  items: [
    {
      public_id: "overdue-1",
      number: "RDM-101",
      status: "assigned",
      status_label: "Назначено",
      planned_start_at: "2026-10-01T08:00:00Z",
    },
    {
      public_id: "overdue-2",
      number: "RDM-102",
      status: "rejected",
      status_label: "Отклонено",
      planned_start_at: "2026-10-01T09:00:00Z",
    },
  ],
  total: 2,
  limit: 20,
  offset: 0,
};

const mockL2Load = {
  items: [
    {
      user_id: 20,
      full_name: "Инженер Иванов",
      assigned: 5,
      completed: 4,
      planned_minutes: 270,
    },
    {
      user_id: 21,
      full_name: "Инженер Петров",
      assigned: 3,
      completed: 2,
      planned_minutes: 180,
    },
  ],
};

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("FE-04 ReportsWorkspace", () => {
  it("renders nothing and makes no API calls when user lacks Manager or Admin roles", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    // L1 user
    const wrapperL1 = mount(ReportsWorkspace, {
      props: {
        currentUser: { id: 10, username: "l1_user", roles: [1] },
      },
    });
    await flushPromises();
    expect(wrapperL1.find(".reports-workspace").exists()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(0);

    // L2 user
    const wrapperL2 = mount(ReportsWorkspace, {
      props: {
        currentUser: { id: 20, username: "l2_user", roles: [{ id: 2, name: "L2" }] },
      },
    });
    await flushPromises();
    expect(wrapperL2.find(".reports-workspace").exists()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(0);
  });

  it("renders reports workspace for MANAGER and ADMIN roles", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/reports/summary")) return ok(mockSummary);
      if (url.includes("/api/v1/reports/overdue")) return ok(mockOverdue);
      if (url.includes("/api/v1/reports/l2-load")) return ok(mockL2Load);
      return fail(404, "not_found");
    });

    const wrapper = mount(ReportsWorkspace, {
      props: {
        currentUser: { id: 30, username: "manager_user", roles: [3] },
        timezone: "Asia/Yekaterinburg",
      },
    });
    await flushPromises();

    expect(wrapper.find(".reports-workspace").exists()).toBe(true);
    expect(wrapper.text()).toContain("Сводные отчёты");
    expect(wrapper.find('[data-test="stat-created"]').text()).toContain("12");
    expect(wrapper.find('[data-test="stat-completed"]').text()).toContain("8");
  });

  it("formats share as percentage and displays 'нет данных' when value is null", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/reports/summary")) return ok(mockSummary);
      if (url.includes("/api/v1/reports/overdue")) return ok(mockOverdue);
      if (url.includes("/api/v1/reports/l2-load")) return ok(mockL2Load);
      return fail(404, "not_found");
    });

    const wrapper = mount(ReportsWorkspace, {
      props: {
        currentUser: { id: 1, username: "admin", roles: [4] },
      },
    });
    await flushPromises();

    // 0.1666666667 -> 16.7% (2 из 12)
    const rejectedStat = wrapper.find('[data-test="stat-rejected-share"]').text();
    expect(rejectedStat).toContain("16.7%");
    expect(rejectedStat).toContain("2 из 12");

    // null value -> "нет данных", must NOT be "0%"
    const repeatRejectedStat = wrapper.find('[data-test="stat-repeat-rejected-share"]').text();
    expect(repeatRejectedStat).toContain("нет данных");
    expect(repeatRejectedStat).not.toContain("0.0%");
    expect(repeatRejectedStat).not.toContain("0%");
  });

  it("converts period in user's profile timezone and queries report endpoints", async () => {
    const urlsRequested: string[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      urlsRequested.push(url);
      if (url.includes("/api/v1/reports/summary")) return ok(mockSummary);
      if (url.includes("/api/v1/reports/overdue")) return ok(mockOverdue);
      if (url.includes("/api/v1/reports/l2-load")) return ok(mockL2Load);
      return fail(404, "not_found");
    });

    const wrapper = mount(ReportsWorkspace, {
      props: {
        currentUser: { id: 1, username: "admin", roles: [4] },
        timezone: "Asia/Yekaterinburg",
      },
    });
    await flushPromises();

    // Set custom date range
    await wrapper.find('[data-test="input-report-from"]').setValue("2026-10-01");
    await wrapper.find('[data-test="input-report-to"]').setValue("2026-10-05");
    await wrapper.find('[data-test="btn-apply-period"]').trigger("click");
    await flushPromises();

    const summaryCalls = urlsRequested.filter((u) => u.includes("/api/v1/reports/summary?from="));
    const lastSummaryCall = summaryCalls[summaryCalls.length - 1];
    expect(lastSummaryCall).toBeDefined();
    // In Asia/Yekaterinburg (UTC+5), 2026-10-01 00:00 is 2026-09-30T19:00:00.000Z
    expect(decodeURIComponent(lastSummaryCall!)).toContain("2026-09-30T19:00:00.000Z");
  });

  it("renders overdue cards and L2 load tables without showing internal case_id", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/reports/summary")) return ok(mockSummary);
      if (url.includes("/api/v1/reports/overdue")) return ok(mockOverdue);
      if (url.includes("/api/v1/reports/l2-load")) return ok(mockL2Load);
      return fail(404, "not_found");
    });

    const wrapper = mount(ReportsWorkspace, {
      props: {
        currentUser: { id: 1, username: "admin", roles: [4] },
      },
    });
    await flushPromises();

    // Overdue table
    expect(wrapper.find('[data-test="table-overdue"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("RDM-101");
    expect(wrapper.text()).toContain("RDM-102");
    // Ensure case_id is not exposed in markup
    expect(wrapper.html()).not.toContain("case_id");

    // L2 load table
    expect(wrapper.find('[data-test="table-l2-load"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("Инженер Иванов");
    expect(wrapper.text()).toContain("4 ч 30 мин");
    expect(wrapper.text()).toContain("Инженер Петров");
    expect(wrapper.text()).toContain("3 ч");
  });
});
