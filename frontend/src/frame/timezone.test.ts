import { describe, expect, it } from "vitest";
import {
  COMMON_TIMEZONES,
  convertWallTimeToISO,
  detectBrowserTimezone,
  formatInTimezone,
  isValidTimezone,
} from "./timezone";

describe("timezone utilities", () => {
  it("detects a valid browser timezone or defaults to Europe/Moscow", () => {
    const tz = detectBrowserTimezone();
    expect(typeof tz).toBe("string");
    expect(isValidTimezone(tz)).toBe(true);
  });

  it("validates valid and invalid IANA timezone identifiers", () => {
    expect(isValidTimezone("Europe/Moscow")).toBe(true);
    expect(isValidTimezone("Asia/Yekaterinburg")).toBe(true);
    expect(isValidTimezone("UTC")).toBe(true);
    expect(isValidTimezone("Invalid/Timezone_XYZ")).toBe(false);
    expect(isValidTimezone("")).toBe(false);
  });

  it("includes all major Russian timezones in COMMON_TIMEZONES", () => {
    const values = COMMON_TIMEZONES.map((t) => t.value);
    expect(values).toContain("Europe/Moscow");
    expect(values).toContain("Asia/Yekaterinburg");
    expect(values).toContain("Asia/Novosibirsk");
    expect(values).toContain("Asia/Vladivostok");
    expect(values).toContain("UTC");
  });

  it("converts wall time in Europe/Moscow (UTC+3) to correct UTC ISO string", () => {
    // 2026-10-15 14:30 Moscow time = 11:30 UTC
    const iso = convertWallTimeToISO("2026-10-15", "14:30", "Europe/Moscow");
    const date = new Date(iso);
    expect(date.getUTCFullYear()).toBe(2026);
    expect(date.getUTCMonth()).toBe(9); // 0-indexed October
    expect(date.getUTCDate()).toBe(15);
    expect(date.getUTCHours()).toBe(11);
    expect(date.getUTCMinutes()).toBe(30);
  });

  it("converts wall time in Asia/Yekaterinburg (UTC+5) to correct UTC ISO string", () => {
    // 2026-10-15 14:30 Yekaterinburg time = 09:30 UTC
    const iso = convertWallTimeToISO("2026-10-15", "14:30", "Asia/Yekaterinburg");
    const date = new Date(iso);
    expect(date.getUTCFullYear()).toBe(2026);
    expect(date.getUTCMonth()).toBe(9);
    expect(date.getUTCDate()).toBe(15);
    expect(date.getUTCHours()).toBe(9);
    expect(date.getUTCMinutes()).toBe(30);
  });

  it("converts wall time in UTC to exact ISO string", () => {
    const iso = convertWallTimeToISO("2026-10-15", "14:30", "UTC");
    const date = new Date(iso);
    expect(date.getUTCHours()).toBe(14);
    expect(date.getUTCMinutes()).toBe(30);
  });

  it("formats ISO date string in target timezone correctly", () => {
    const iso = "2026-10-15T09:30:00.000Z";
    const formattedMsk = formatInTimezone(iso, "Europe/Moscow");
    expect(formattedMsk).toContain("12:30"); // 09:30 UTC + 3 = 12:30

    const formattedYekt = formatInTimezone(iso, "Asia/Yekaterinburg");
    expect(formattedYekt).toContain("14:30"); // 09:30 UTC + 5 = 14:30
  });

  it("throws error for invalid date or time inputs in convertWallTimeToISO", () => {
    expect(() => convertWallTimeToISO("", "14:30", "Europe/Moscow")).toThrow();
    expect(() => convertWallTimeToISO("2026-10-15", "", "Europe/Moscow")).toThrow();
    expect(() => convertWallTimeToISO("invalid", "invalid", "Europe/Moscow")).toThrow();
    expect(() => convertWallTimeToISO("2026-02-31", "14:30", "Europe/Moscow")).toThrow();
  });

  it("rejects nonexistent and ambiguous local times at daylight saving transitions", () => {
    expect(() => convertWallTimeToISO("2026-03-08", "02:30", "America/New_York")).toThrow();
    expect(() => convertWallTimeToISO("2026-11-01", "01:30", "America/New_York")).toThrow();
  });
});
