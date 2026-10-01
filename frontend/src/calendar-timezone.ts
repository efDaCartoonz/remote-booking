/**
 * Calendar timezone & DST helpers for Manager Calendar views.
 */

/**
 * Returns the timezone offset in milliseconds between UTC and the specified IANA timeZone at a given date.
 * Positive offset means the local time is ahead of UTC (e.g. UTC+5 returns +18,000,000 ms).
 */
export function getTzOffsetMs(date: Date, timeZone: string): number {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const parts = formatter.formatToParts(date);
  const map: Record<string, string> = {};
  for (const p of parts) map[p.type] = p.value;
  const hour = Number(map.hour) % 24;
  const asUtc = Date.UTC(
    Number(map.year),
    Number(map.month) - 1,
    Number(map.day),
    hour,
    Number(map.minute),
    Number(map.second)
  );
  return asUtc - date.getTime();
}

/**
 * Given a "YYYY-MM-DD" date string and a day offset, returns the Date corresponding to
 * local midnight (00:00:00) in the specified timeZone on that calendar day.
 */
export function dateInTz(dateValue: string, dayOffset = 0, timeZone = "Asia/Yekaterinburg"): Date {
  const [year, month, day] = dateValue.split("-").map(Number);
  const baseUtc = new Date(Date.UTC(year, month - 1, day + dayOffset, 0, 0, 0));
  const offset1 = getTzOffsetMs(baseUtc, timeZone);
  const adjusted = new Date(baseUtc.getTime() - offset1);
  const offset2 = getTzOffsetMs(adjusted, timeZone);
  return new Date(baseUtc.getTime() - offset2);
}

/**
 * Returns formatted "YYYY-MM-DD" date string in the specified timeZone.
 */
export function toTzDateString(date: Date, timeZone: string): string {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const parts = formatter.formatToParts(date);
  const map: Record<string, string> = {};
  for (const p of parts) map[p.type] = p.value;
  return `${map.year}-${map.month}-${map.day}`;
}

export interface DayBounds {
  dayStart: Date;
  dayEnd: Date;
  durationMs: number;
  isDstTransition: boolean;
}

/**
 * Computes day start (00:00:00 local) and next day start (00:00:00 local) in timeZone,
 * duration in milliseconds, and whether this day has a DST transition.
 */
export function getDayBoundsInTz(day: Date, timeZone: string): DayBounds {
  const dateStr = toTzDateString(day, timeZone);
  const dayStart = dateInTz(dateStr, 0, timeZone);
  const dayEnd = dateInTz(dateStr, 1, timeZone);
  const durationMs = dayEnd.getTime() - dayStart.getTime();

  // A standard non-transition day has exactly 24 hours (86,400,000 ms)
  const is24h = durationMs === 24 * 60 * 60 * 1000;

  // Also check if the offset at start of day differs from end of day (e.g. midnight transitions)
  const startOffset = getTzOffsetMs(dayStart, timeZone);
  const endOffset = getTzOffsetMs(new Date(dayEnd.getTime() - 1000), timeZone);
  const offsetChanged = startOffset !== endOffset;

  return {
    dayStart,
    dayEnd,
    durationMs,
    isDstTransition: !is24h || offsetChanged,
  };
}

/**
 * Returns true if the specified calendar day experiences a DST transition
 * (e.g. spring-forward or fall-back) in the given timeZone.
 */
export function isDayDstTransition(day: Date, timeZone: string): boolean {
  return getDayBoundsInTz(day, timeZone).isDstTransition;
}

/**
 * Returns true if any day in the given list of calendar days has a DST transition.
 */
export function hasDstTransitionInRange(days: Date[], timeZone: string): boolean {
  return days.some((day) => isDayDstTransition(day, timeZone));
}
