import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ScheduleEditor from "./ScheduleEditor.vue";

const originalFetch = globalThis.fetch;

const ok = (body: unknown): Response => ({ ok: true, status: 200, json: async () => body }) as Response;

const employees = [
  { id: 2, full_name: "Инженер Второй Линии", timezone: "Asia/Yekaterinburg", roles: [2] },
  { id: 3, full_name: "Специалист Первой Линии", timezone: "Europe/Moscow", roles: [1] },
];

function mockApi(initialDays: Array<{ day: string; start_time: string; end_time: string }> = []) {
  const puts: Array<{ url: string; body: any }> = [];
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "PUT") {
      puts.push({ url, body: JSON.parse(String(init.body)) });
      return ok({ user_id: 2, timezone: "Asia/Yekaterinburg", days: [] });
    }
    if (url.endsWith("/schedules/employees")) return ok(employees);
    if (url.includes("/schedules/days")) {
      return ok({ date_from: "", date_to: "", users: initialDays.length ? [{ user_id: 2, timezone: "Asia/Yekaterinburg", days: initialDays }] : [] });
    }
    if (url.includes("/calendar/days")) return ok([{ date: "2026-10-12", day_type_code: 2, is_manual_override: false }]);
    return ok([]);
  }) as typeof fetch;
  return puts;
}

describe("ScheduleEditor", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-15T09:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("lists the employees returned for planning and shows existing shifts per date", async () => {
    mockApi([{ day: "2026-10-05", start_time: "07:00:00", end_time: "16:00:00" }]);
    const wrapper = mount(ScheduleEditor);
    await flushPromises();

    expect(wrapper.find('[data-test="sched-month"]').text()).toContain("Октябрь 2026");
    expect(wrapper.find('[data-test="sched-row-2"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="sched-row-3"]').text()).toContain("L1");
    expect(wrapper.find('[data-test="sched-row-1"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="cell-2-2026-10-05"]').text()).toBe("7–16");
    expect(wrapper.find('[data-test="cell-2-2026-10-06"]').text()).toBe("—");
    expect(wrapper.find('[data-test="sched-save"]').attributes("disabled")).toBeDefined();
  });

  it("edits a single day independently and saves only the changed employee for the month", async () => {
    const puts = mockApi([{ day: "2026-10-05", start_time: "07:00:00", end_time: "16:00:00" }]);
    const wrapper = mount(ScheduleEditor);
    await flushPromises();

    await wrapper.find('[data-test="cell-2-2026-10-07"]').trigger("click");
    await wrapper.find('[data-test="cell-start"]').setValue("13:00");
    await wrapper.find('[data-test="cell-end"]').setValue("22:00");
    await wrapper.find('[data-test="cell-apply"]').trigger("click");
    expect(wrapper.find('[data-test="cell-2-2026-10-07"]').text()).toBe("13–22");

    await wrapper.find('[data-test="sched-save"]').trigger("click");
    await flushPromises();

    expect(puts).toHaveLength(1);
    expect(puts[0].url).toContain("/api/v1/admin/schedules/2/days");
    expect(puts[0].body).toMatchObject({ date_from: "2026-10-01", date_to: "2026-10-31", timezone: "Asia/Yekaterinburg" });
    expect(puts[0].body.days).toEqual([
      { day: "2026-10-05", start_time: "07:00:00", end_time: "16:00:00" },
      { day: "2026-10-07", start_time: "13:00:00", end_time: "22:00:00" },
    ]);
  });

  it("marks a day as day off and rejects an inverted interval", async () => {
    mockApi([{ day: "2026-10-05", start_time: "07:00:00", end_time: "16:00:00" }]);
    const wrapper = mount(ScheduleEditor);
    await flushPromises();

    await wrapper.find('[data-test="cell-2-2026-10-05"]').trigger("click");
    await wrapper.find('[data-test="cell-off"]').trigger("click");
    expect(wrapper.find('[data-test="cell-2-2026-10-05"]').text()).toBe("—");

    await wrapper.find('[data-test="cell-2-2026-10-06"]').trigger("click");
    await wrapper.find('[data-test="cell-start"]').setValue("18:00");
    await wrapper.find('[data-test="cell-end"]').setValue("09:00");
    await wrapper.find('[data-test="cell-apply"]').trigger("click");
    expect(wrapper.find('[data-test="sched-error"]').text()).toContain("раньше");
    expect(wrapper.find('[data-test="cell-2-2026-10-06"]').text()).toBe("—");
  });

  it("fills a 2/2 pattern and keeps the holiday free by default", async () => {
    mockApi();
    const wrapper = mount(ScheduleEditor);
    await flushPromises();

    await wrapper.find('[data-test="sched-template-2"]').trigger("click");
    await wrapper.find('[data-test="gen-kind"]').setValue("cycle");
    await wrapper.find('[data-test="gen-from"]').setValue("2026-10-10");
    await wrapper.find('[data-test="gen-to"]').setValue("2026-10-15");
    await wrapper.find('[data-test="gen-cycle-start"]').setValue("2026-10-11");
    await wrapper.find('[data-test="gen-apply"]').trigger("click");

    // cycle: 11, 12 work; 13, 14 rest; 15 work. 12 is a holiday and stays free.
    expect(wrapper.find('[data-test="cell-2-2026-10-10"]').text()).toBe("—");
    expect(wrapper.find('[data-test="cell-2-2026-10-11"]').text()).toBe("9–18");
    expect(wrapper.find('[data-test="cell-2-2026-10-12"]').text()).toBe("—");
    expect(wrapper.find('[data-test="cell-2-2026-10-13"]').text()).toBe("—");
    expect(wrapper.find('[data-test="cell-2-2026-10-15"]').text()).toBe("9–18");
  });

  it("discards unsaved edits", async () => {
    mockApi();
    const wrapper = mount(ScheduleEditor);
    await flushPromises();

    await wrapper.find('[data-test="cell-2-2026-10-07"]').trigger("click");
    await wrapper.find('[data-test="cell-apply"]').trigger("click");
    expect(wrapper.find('[data-test="sched-save"]').attributes("disabled")).toBeUndefined();
    await wrapper.find('[data-test="sched-discard"]').trigger("click");
    expect(wrapper.find('[data-test="cell-2-2026-10-07"]').text()).toBe("—");
    expect(wrapper.find('[data-test="sched-save"]').attributes("disabled")).toBeDefined();
  });
});
