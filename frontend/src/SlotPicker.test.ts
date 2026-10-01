import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import SlotPicker from "./SlotPicker.vue";

function mountPicker(props: Partial<{ modelValue: string; min: string; max: string }> = {}) {
  return mount(SlotPicker, {
    props: { modelValue: "", min: "2026-10-05T11:15", max: "2026-10-07T14:30", ...props },
  });
}

const lastEmit = (wrapper: ReturnType<typeof mountPicker>) => {
  const events = wrapper.emitted("update:modelValue") ?? [];
  return events[events.length - 1];
};

const optionValues = (wrapper: ReturnType<typeof mountPicker>, name: string) =>
  wrapper.findAll(`[data-test="slot-${name}"] option`).map((o) => o.attributes("value")).filter(Boolean);

describe("SlotPicker", () => {
  it("offers only dates inside the window", () => {
    const wrapper = mountPicker();
    expect(optionValues(wrapper, "date")).toEqual(["2026-10-05", "2026-10-06", "2026-10-07"]);
  });

  it("limits the time list to the window on the first and last day, in 15 minute steps", async () => {
    const wrapper = mountPicker();
    await wrapper.find('[data-test="slot-date"]').setValue("2026-10-05");
    const first = optionValues(wrapper, "time");
    expect(first[0]).toBe("11:15");
    expect(first[first.length - 1]).toBe("23:45");
    expect(first).not.toContain("11:00");
    expect(first).not.toContain("11:20");

    await wrapper.find('[data-test="slot-date"]').setValue("2026-10-06");
    expect(optionValues(wrapper, "time")).toHaveLength(96);

    await wrapper.find('[data-test="slot-date"]').setValue("2026-10-07");
    const last = optionValues(wrapper, "time");
    expect(last[0]).toBe("00:00");
    expect(last[last.length - 1]).toBe("14:30");
  });

  it("emits a datetime-local compatible value only when both parts are chosen", async () => {
    const wrapper = mountPicker();
    await wrapper.find('[data-test="slot-date"]').setValue("2026-10-06");
    expect(lastEmit(wrapper)).toEqual([""]);
    await wrapper.find('[data-test="slot-time"]').setValue("09:30");
    expect(lastEmit(wrapper)).toEqual(["2026-10-06T09:30"]);
  });

  it("moves the time into range when the date changes to a restricted day", async () => {
    const wrapper = mountPicker({ modelValue: "2026-10-06T09:00" });
    await wrapper.find('[data-test="slot-date"]').setValue("2026-10-05");
    expect(lastEmit(wrapper)).toEqual(["2026-10-05T11:15"]);
  });

  it("keeps an existing value that lies outside the grid visible", () => {
    const wrapper = mountPicker({ modelValue: "2026-10-06T09:20" });
    expect(optionValues(wrapper, "time")).toContain("09:20");
  });
});
