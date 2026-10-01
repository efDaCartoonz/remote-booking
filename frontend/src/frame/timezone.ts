export interface TimezoneOption {
  value: string;
  label: string;
  offset: string;
}

export const COMMON_TIMEZONES: TimezoneOption[] = [
  { value: "Europe/Kaliningrad", label: "Калининград (UTC+2)", offset: "+02:00" },
  { value: "Europe/Moscow", label: "Москва, Санкт-Петербург (UTC+3)", offset: "+03:00" },
  { value: "Europe/Samara", label: "Самара (UTC+4)", offset: "+04:00" },
  { value: "Asia/Yekaterinburg", label: "Екатеринбург (UTC+5)", offset: "+05:00" },
  { value: "Asia/Omsk", label: "Омск (UTC+6)", offset: "+06:00" },
  { value: "Asia/Novosibirsk", label: "Новосибирск (UTC+7)", offset: "+07:00" },
  { value: "Asia/Krasnoyarsk", label: "Красноярск (UTC+7)", offset: "+07:00" },
  { value: "Asia/Irkutsk", label: "Иркутск (UTC+8)", offset: "+08:00" },
  { value: "Asia/Yakutsk", label: "Якутск (UTC+9)", offset: "+09:00" },
  { value: "Asia/Vladivostok", label: "Владивосток (UTC+10)", offset: "+10:00" },
  { value: "Asia/Magadan", label: "Магадан (UTC+11)", offset: "+11:00" },
  { value: "Asia/Kamchatka", label: "Камчатка (UTC+12)", offset: "+12:00" },
  { value: "UTC", label: "UTC (UTC+0)", offset: "+00:00" },
];

export function isValidTimezone(timeZone: string): boolean {
  try {
    Intl.DateTimeFormat(undefined, { timeZone });
    return true;
  } catch {
    return false;
  }
}

export function detectBrowserTimezone(): string {
  try {
    const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (detected && isValidTimezone(detected)) {
      return detected;
    }
  } catch {
    // ignore
  }
  return "Europe/Moscow";
}

/**
 * Converts a local wall time (dateStr: YYYY-MM-DD, timeStr: HH:mm) in a given IANA timezone
 * into an ISO 8601 string (with UTC 'Z' or timezone offset) representing that exact instant.
 */
export function convertWallTimeToISO(dateStr: string, timeStr: string, timeZone: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr) || !/^\d{2}:\d{2}$/.test(timeStr)) {
    throw new Error("Date and time are required");
  }

  const [yearStr, monthStr, dayStr] = dateStr.split("-");
  const [hourStr, minStr] = timeStr.split(":");

  const year = parseInt(yearStr, 10);
  const month = parseInt(monthStr, 10);
  const day = parseInt(dayStr, 10);
  const hour = parseInt(hourStr, 10);
  const minute = parseInt(minStr, 10);

  if (
    Number.isNaN(year) ||
    Number.isNaN(month) ||
    Number.isNaN(day) ||
    Number.isNaN(hour) ||
    Number.isNaN(minute) ||
    year < 1900 || year > 2100 || month < 1 || month > 12 ||
    day < 1 || day > 31 || hour < 0 || hour > 23 || minute < 0 || minute > 59
  ) {
    throw new Error("Invalid date or time format");
  }
  const calendarDate = new Date(Date.UTC(year, month - 1, day));
  if (calendarDate.getUTCFullYear() !== year || calendarDate.getUTCMonth() !== month - 1 || calendarDate.getUTCDate() !== day) {
    throw new Error("Invalid calendar date");
  }

  // Find the exact UTC timestamp matching wall time in target timezone
  // Start with a rough UTC timestamp assuming UTC
  let utcTimestamp = Date.UTC(year, month - 1, day, hour, minute, 0);

  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    second: "numeric",
    hourCycle: "h23",
  });

  function getWallTimeAt(ts: number): { y: number; m: number; d: number; h: number; min: number } {
    const parts = formatter.formatToParts(new Date(ts));
    const map: Record<string, number> = {};
    for (const part of parts) {
      if (part.type !== "literal") {
        map[part.type] = parseInt(part.value, 10);
      }
    }
    return {
      y: map.year,
      m: map.month,
      d: map.day,
      h: map.hour === 24 ? 0 : map.hour,
      min: map.minute,
    };
  }

  // Iterate to adjust for timezone offset and potential DST
  for (let iter = 0; iter < 3; iter++) {
    const current = getWallTimeAt(utcTimestamp);
    const targetUtcGuess = Date.UTC(current.y, current.m - 1, current.d, current.h, current.min, 0);
    const targetUtcDesired = Date.UTC(year, month - 1, day, hour, minute, 0);
    const diff = targetUtcDesired - targetUtcGuess;
    if (diff === 0) break;
    utcTimestamp += diff;
  }

  const target = { y: year, m: month, d: day, h: hour, min: minute };
  const matches = (ts: number) => {
    const wall = getWallTimeAt(ts);
    return wall.y === target.y && wall.m === target.m && wall.d === target.d && wall.h === target.h && wall.min === target.min;
  };
  if (!matches(utcTimestamp)) throw new Error("Nonexistent local time");
  for (let offset = -180; offset <= 180; offset += 15) {
    if (offset !== 0 && matches(utcTimestamp + offset * 60_000)) {
      throw new Error("Ambiguous local time");
    }
  }

  return new Date(utcTimestamp).toISOString();
}

/**
 * Formats an ISO 8601 date string for display in the specified IANA timezone.
 */
export function formatInTimezone(isoStr: string, timeZone: string): string {
  try {
    const date = new Date(isoStr);
    if (Number.isNaN(date.getTime())) return isoStr;
    const formatter = new Intl.DateTimeFormat("ru-RU", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
    return formatter.format(date);
  } catch {
    return isoStr;
  }
}
