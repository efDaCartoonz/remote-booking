import { describe, expect, it } from "vitest";
import {
  addDays,
  copyPeriod,
  enumerateDates,
  generatePattern,
  isoWeekday,
  monthRange,
  quarterHourOptions,
  shiftLabel,
} from "./scheduleGenerator";

const times = { start: "07:00", end: "16:00" };

describe("scheduleGenerator", () => {
  it("handles dates without time zone drift", () => {
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(isoWeekday("2026-10-05")).toBe(1);
    expect(isoWeekday("2026-10-11")).toBe(7);
    expect(monthRange(2026, 2)).toEqual({ from: "2026-02-01", to: "2026-02-28" });
    expect(enumerateDates("2026-10-01", "2026-10-03")).toEqual(["2026-10-01", "2026-10-02", "2026-10-03"]);
    expect(enumerateDates("2026-10-03", "2026-10-01")).toEqual([]);
  });

  it("builds weekday patterns and keeps holidays free", () => {
    const map = generatePattern({
      from: "2026-10-05",
      to: "2026-10-11",
      kind: "weekdays",
      weekdays: [1, 2, 3, 4, 5],
      times,
      skipDates: new Set(["2026-10-07"]),
    });
    expect(Object.keys(map)).toEqual(["2026-10-05", "2026-10-06", "2026-10-08", "2026-10-09"]);
  });

  it("builds a 2/2 cycle from the chosen start", () => {
    const map = generatePattern({
      from: "2026-10-01",
      to: "2026-10-08",
      kind: "cycle",
      workDays: 2,
      restDays: 2,
      cycleStart: "2026-10-02",
      times,
    });
    expect(Object.keys(map)).toEqual(["2026-10-02", "2026-10-03", "2026-10-06", "2026-10-07"]);
  });

  it("supports every day and rejects an invalid interval", () => {
    expect(Object.keys(generatePattern({ from: "2026-10-01", to: "2026-10-03", kind: "every_day", times }))).toHaveLength(3);
    expect(() =>
      generatePattern({ from: "2026-10-01", to: "2026-10-03", kind: "every_day", times: { start: "16:00", end: "07:00" } }),
    ).toThrow("schedule_start_must_precede_end");
  });

  it("copies a previous period onto a new one", () => {
    const source = { "2026-09-28": times, "2026-09-30": { start: "13:00", end: "22:00" } };
    expect(copyPeriod(source, "2026-09-28", "2026-10-05", 7)).toEqual({
      "2026-10-05": times,
      "2026-10-07": { start: "13:00", end: "22:00" },
    });
    expect(copyPeriod(source, "2026-09-28", "2026-10-05", 7, new Set(["2026-10-07"]))).toEqual({ "2026-10-05": times });
  });

  it("formats labels and offers a quarter-hour grid", () => {
    expect(shiftLabel(undefined)).toBe("—");
    expect(shiftLabel(times)).toBe("7–16");
    expect(shiftLabel({ start: "07:30", end: "16:00" })).toBe("07:30–16");
    const options = quarterHourOptions();
    expect(options[0]).toBe("00:00");
    expect(options).toContain("13:45");
    expect(options[options.length - 1]).toBe("23:59");
  });
});
