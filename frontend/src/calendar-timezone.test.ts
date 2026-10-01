import { describe, expect, it } from "vitest";
import {
  dateInTz,
  getDayBoundsInTz,
  getTzOffsetMs,
  hasDstTransitionInRange,
  isDayDstTransition,
  toTzDateString,
} from "./calendar-timezone";

describe("calendar-timezone helpers", () => {
  describe("isDayDstTransition", () => {
    it("detects spring-forward transition (23h day) in Europe/London", () => {
      // 2026-03-29: Clocks jump 01:00 -> 02:00
      const transitionDay = dateInTz("2026-03-29", 0, "Europe/London");
      expect(isDayDstTransition(transitionDay, "Europe/London")).toBe(true);

      const bounds = getDayBoundsInTz(transitionDay, "Europe/London");
      expect(bounds.durationMs).toBe(23 * 60 * 60 * 1000);
      expect(bounds.isDstTransition).toBe(true);
    });

    it("detects fall-back transition (25h day) in Europe/London", () => {
      // 2026-10-25: Clocks repeat 01:00 -> 02:00
      const transitionDay = dateInTz("2026-10-25", 0, "Europe/London");
      expect(isDayDstTransition(transitionDay, "Europe/London")).toBe(true);

      const bounds = getDayBoundsInTz(transitionDay, "Europe/London");
      expect(bounds.durationMs).toBe(25 * 60 * 60 * 1000);
      expect(bounds.isDstTransition).toBe(true);
    });

    it("detects spring-forward transition (23h day) in America/New_York", () => {
      // 2026-03-08: Clocks jump 02:00 -> 03:00
      const transitionDay = dateInTz("2026-03-08", 0, "America/New_York");
      expect(isDayDstTransition(transitionDay, "America/New_York")).toBe(true);

      const bounds = getDayBoundsInTz(transitionDay, "America/New_York");
      expect(bounds.durationMs).toBe(23 * 60 * 60 * 1000);
    });

    it("detects fall-back transition (25h day) in America/New_York", () => {
      // 2026-11-01: Clocks repeat 01:00 -> 02:00
      const transitionDay = dateInTz("2026-11-01", 0, "America/New_York");
      expect(isDayDstTransition(transitionDay, "America/New_York")).toBe(true);

      const bounds = getDayBoundsInTz(transitionDay, "America/New_York");
      expect(bounds.durationMs).toBe(25 * 60 * 60 * 1000);
    });

    it("returns false for non-transition days in DST zone", () => {
      const normalDay = dateInTz("2026-07-15", 0, "Europe/London");
      expect(isDayDstTransition(normalDay, "Europe/London")).toBe(false);

      const bounds = getDayBoundsInTz(normalDay, "Europe/London");
      expect(bounds.durationMs).toBe(24 * 60 * 60 * 1000);
      expect(bounds.isDstTransition).toBe(false);
    });

    it("returns false for all days in non-DST zones (Asia/Yekaterinburg, Europe/Moscow, UTC)", () => {
      const testDays = ["2026-03-29", "2026-10-25", "2026-01-01", "2026-06-15"];
      for (const timeZone of ["Asia/Yekaterinburg", "Europe/Moscow", "UTC"]) {
        for (const dayStr of testDays) {
          const day = dateInTz(dayStr, 0, timeZone);
          expect(isDayDstTransition(day, timeZone)).toBe(false);
          const bounds = getDayBoundsInTz(day, timeZone);
          expect(bounds.durationMs).toBe(24 * 60 * 60 * 1000);
        }
      }
    });
  });

  describe("hasDstTransitionInRange", () => {
    it("returns true if a week in Europe/London contains the transition day", () => {
      // Week of March 23-29, 2026
      const weekDays = Array.from({ length: 7 }, (_, i) => dateInTz("2026-03-23", i, "Europe/London"));
      expect(hasDstTransitionInRange(weekDays, "Europe/London")).toBe(true);
    });

    it("returns false if a week in Europe/London does not contain a transition", () => {
      // Week of July 6-12, 2026
      const weekDays = Array.from({ length: 7 }, (_, i) => dateInTz("2026-07-06", i, "Europe/London"));
      expect(hasDstTransitionInRange(weekDays, "Europe/London")).toBe(false);
    });

    it("returns false for any week in Asia/Yekaterinburg", () => {
      const weekDays = Array.from({ length: 7 }, (_, i) => dateInTz("2026-03-23", i, "Asia/Yekaterinburg"));
      expect(hasDstTransitionInRange(weekDays, "Asia/Yekaterinburg")).toBe(false);
    });
  });

  describe("dateInTz and toTzDateString precision", () => {
    it("preserves exact local midnight across dates", () => {
      const d = dateInTz("2026-10-01", 0, "Asia/Yekaterinburg");
      expect(toTzDateString(d, "Asia/Yekaterinburg")).toBe("2026-10-01");
      expect(d.toISOString()).toBe("2026-09-30T19:00:00.000Z"); // 00:00 in UTC+5
    });

    it("calculates offset accurately", () => {
      const d = new Date("2026-10-01T12:00:00Z");
      expect(getTzOffsetMs(d, "Asia/Yekaterinburg")).toBe(5 * 60 * 60 * 1000);
      expect(getTzOffsetMs(d, "Europe/Moscow")).toBe(3 * 60 * 60 * 1000);
      expect(getTzOffsetMs(d, "UTC")).toBe(0);
    });
  });
});
