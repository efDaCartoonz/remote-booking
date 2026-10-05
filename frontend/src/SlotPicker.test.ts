import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import SlotPicker from "./SlotPicker.vue";

function mountPicker(props: Partial<{ modelValue: string; min: string; max: string }> = {}) {
  return mount(SlotPicker, {
    props: { modelValue: "", min: "2026-10-05T11:15", max: "2026-10-07T14:30", ...props },
  });
}

const options = (wrapper: ReturnType<typeof mountPicker>, name: string) =>
  wrapper.findAll(`[data-test="slot-${name}"] option`).map((o) => o.attributes("value")).filter(Boolean);

const lastEmit = (wrapper: ReturnType<typeof mountPicker>) => {
  const events = wrapper.emitted("update:modelValue") ?? [];
  return events[events.length - 1];
};

async function choose(wrapper: ReturnType<typeof mountPicker>, date: string, hour?: string, minute?: string) {
  await wrapper.find('[data-test="slot-date"]').setValue(date);
  if (hour) await wrapper.find('[data-test="slot-hour"]').setValue(hour);
  if (minute) await wrapper.find('[data-test="slot-minute"]').setValue(minute);
}

describe("SlotPicker", () => {
  it("limits the calendar to the window and shows the weekday", async () => {
    const wrapper = mountPicker();
    const input = wrapper.find('[data-test="slot-date"]');
    expect(input.attributes("type")).toBe("date");
    expect(input.attributes("min")).toBe("2026-10-05");
    expect(input.attributes("max")).toBe("2026-10-07");
    await choose(wrapper, "2026-10-06");
    expect(wrapper.find('[data-test="slot-weekday"]').text()).toBe("вторник");
  });

  it("offers separate hours and minutes in 5 minute steps within the window", async () => {
    const wrapper = mountPicker();
    await choose(wrapper, "2026-10-05");
    const firstDayHours = options(wrapper, "hour");
    expect(firstDayHours[0]).toBe("11");
    expect(firstDayHours[firstDayHours.length - 1]).toBe("23");

    await wrapper.find('[data-test="slot-hour"]').setValue("11");
    expect(options(wrapper, "minute")[0]).toBe("15");
    expect(options(wrapper, "minute")).toHaveLength(9);
    await wrapper.find('[data-test="slot-hour"]').setValue("12");
    expect(options(wrapper, "minute")).toHaveLength(12);
    expect(options(wrapper, "minute")).not.toContain("07");

    await choose(wrapper, "2026-10-06");
    expect(options(wrapper, "hour")).toHaveLength(24);

    await choose(wrapper, "2026-10-07");
    const lastDayHours = options(wrapper, "hour");
    expect(lastDayHours[lastDayHours.length - 1]).toBe("14");
    await wrapper.find('[data-test="slot-hour"]').setValue("14");
    const last = options(wrapper, "minute");
    expect(last[last.length - 1]).toBe("30");
  });

  it("emits a datetime-local compatible value only when date, hour and minute are chosen", async () => {
    const wrapper = mountPicker();
    await choose(wrapper, "2026-10-06");
    expect(lastEmit(wrapper)).toEqual([""]);
    await wrapper.find('[data-test="slot-hour"]').setValue("09");
    await wrapper.find('[data-test="slot-minute"]').setValue("30");
    expect(lastEmit(wrapper)).toEqual(["2026-10-06T09:30"]);
  });

  it("moves the time into range when the date changes to a restricted day", async () => {
    const wrapper = mountPicker({ modelValue: "2026-10-06T09:00" });
    await choose(wrapper, "2026-10-05");
    expect(lastEmit(wrapper)).toEqual(["2026-10-05T11:15"]);
    await choose(wrapper, "2026-10-07");
    expect(lastEmit(wrapper)).toEqual(["2026-10-07T11:15"]);
  });

  it("keeps an existing value that lies outside the step grid visible", () => {
    const wrapper = mountPicker({ modelValue: "2026-10-06T09:23" });
    expect(options(wrapper, "minute")).toContain("23");
    expect(options(wrapper, "hour")).toContain("09");
  });
});
