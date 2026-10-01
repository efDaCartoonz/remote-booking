import { convertWallTimeToISO } from "../frame/timezone";

export const STANDARD_TIMEZONES = [
  "Asia/Yekaterinburg",
  "Europe/Moscow",
  "Europe/Kaliningrad",
  "Europe/Samara",
  "Asia/Omsk",
  "Asia/Novosibirsk",
  "Asia/Krasnoyarsk",
  "Asia/Irkutsk",
  "Asia/Yakutsk",
  "Asia/Vladivostok",
  "Asia/Magadan",
  "Asia/Kamchatka",
  "UTC",
];

export function formatDateInTz(isoStr: string | null | undefined, timeZone = "Asia/Yekaterinburg"): string {
  if (!isoStr) return "—";
  try {
    const date = new Date(isoStr);
    if (Number.isNaN(date.getTime())) return String(isoStr);
    return new Intl.DateTimeFormat("ru-RU", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(date);
  } catch {
    return String(isoStr);
  }
}

export function formatTimeInTz(isoStr: string | null | undefined, timeZone = "Asia/Yekaterinburg"): string {
  if (!isoStr) return "—";
  try {
    const date = new Date(isoStr);
    if (Number.isNaN(date.getTime())) return String(isoStr);
    return new Intl.DateTimeFormat("ru-RU", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  } catch {
    return String(isoStr);
  }
}

export function formatDateTimeInTz(isoStr: string | null | undefined, timeZone = "Asia/Yekaterinburg"): string {
  if (!isoStr) return "—";
  try {
    const date = new Date(isoStr);
    if (Number.isNaN(date.getTime())) return String(isoStr);
    return new Intl.DateTimeFormat("ru-RU", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  } catch {
    return String(isoStr);
  }
}

export function getTodayDateString(timeZone = "Asia/Yekaterinburg"): string {
  try {
    const formatter = new Intl.DateTimeFormat("en-US", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    const parts = formatter.formatToParts(new Date());
    const map: Record<string, string> = {};
    for (const p of parts) map[p.type] = p.value;
    return `${map.year}-${map.month}-${map.day}`;
  } catch {
    const d = new Date();
    return d.toISOString().slice(0, 10);
  }
}

export function addDays(dateStr: string, days: number): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  const year = dt.getUTCFullYear();
  const month = String(dt.getUTCMonth() + 1).padStart(2, "0");
  const day = String(dt.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Given two local date strings (YYYY-MM-DD) in user's profile timezone,
 * converts them to ISO 8601 UTC strings representing the half-open interval [from, to).
 * 'from' is local 00:00:00 on startDate, 'to' is local 00:00:00 on (endDate + 1 day).
 */
export function calculateReportPeriodUtc(
  startDateStr: string,
  endDateStr: string,
  timeZone = "Asia/Yekaterinburg"
): { fromIso: string; toIso: string } {
  const fromIso = convertWallTimeToISO(startDateStr, "00:00", timeZone);
  const nextDayStr = addDays(endDateStr, 1);
  const toIso = convertWallTimeToISO(nextDayStr, "00:00", timeZone);
  return { fromIso, toIso };
}
