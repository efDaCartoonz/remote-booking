/**
 * Pure helpers for the per-date work schedule editor.
 * Dates are plain "YYYY-MM-DD" strings, so nothing depends on the browser time zone.
 */

export interface ShiftTimes {
  start: string; // "HH:MM"
  end: string;
}

export type ShiftMap = Record<string, ShiftTimes>;

export type PatternKind = "every_day" | "weekdays" | "cycle";

export interface PatternOptions {
  from: string;
  to: string;
  kind: PatternKind;
  times: ShiftTimes;
  /** ISO weekdays 1..7 for kind "weekdays". */
  weekdays?: number[];
  /** Work/rest days for kind "cycle", e.g. 2/2. */
  workDays?: number;
  restDays?: number;
  /** First date of the first working block for kind "cycle" (defaults to `from`). */
  cycleStart?: string;
  /** Dates (holidays) that must stay free. */
  skipDates?: ReadonlySet<string>;
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function toUtc(date: string): number {
  if (!DATE_RE.test(date)) throw new Error(`invalid_date:${date}`);
  const [y, m, d] = date.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

function fromUtc(ms: number): string {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}

export function addDays(date: string, days: number): string {
  return fromUtc(toUtc(date) + days * 86_400_000);
}

export function isoWeekday(date: string): number {
  const day = new Date(toUtc(date)).getUTCDay();
  return day === 0 ? 7 : day;
}

export function enumerateDates(from: string, to: string): string[] {
  const start = toUtc(from);
  const end = toUtc(to);
  if (end < start) return [];
  const result: string[] = [];
  for (let ms = start; ms <= end; ms += 86_400_000) result.push(fromUtc(ms));
  return result;
}

export function monthRange(year: number, month: number): { from: string; to: string } {
  const from = `${year}-${String(month).padStart(2, "0")}-01`;
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return { from, to: `${year}-${String(month).padStart(2, "0")}-${String(last).padStart(2, "0")}` };
}

export function timeToMinutes(value: string): number {
  const match = /^(\d{2}):(\d{2})/.exec(value);
  if (!match) throw new Error(`invalid_time:${value}`);
  return Number(match[1]) * 60 + Number(match[2]);
}

export function isValidShift(times: ShiftTimes): boolean {
  try {
    return timeToMinutes(times.start) < timeToMinutes(times.end);
  } catch {
    return false;
  }
}

/** Builds the working days of a pattern. Days not in the result are days off. */
export function generatePattern(options: PatternOptions): ShiftMap {
  if (!isValidShift(options.times)) throw new Error("schedule_start_must_precede_end");
  const skip = options.skipDates ?? new Set<string>();
  const result: ShiftMap = {};
  const dates = enumerateDates(options.from, options.to);
  const work = Math.max(1, options.workDays ?? 2);
  const rest = Math.max(0, options.restDays ?? 2);
  const cycleOrigin = toUtc(options.cycleStart ?? options.from);
  for (const date of dates) {
    if (skip.has(date)) continue;
    let working = false;
    if (options.kind === "every_day") working = true;
    else if (options.kind === "weekdays") working = (options.weekdays ?? []).includes(isoWeekday(date));
    else {
      const offset = Math.round((toUtc(date) - cycleOrigin) / 86_400_000);
      const position = ((offset % (work + rest)) + (work + rest)) % (work + rest);
      working = position < work;
    }
    if (working) result[date] = { ...options.times };
  }
  return result;
}

/** Copies a block of days onto a later period by shifting whole days. */
export function copyPeriod(
  source: ShiftMap,
  sourceFrom: string,
  targetFrom: string,
  length: number,
  skipDates: ReadonlySet<string> = new Set(),
): ShiftMap {
  const result: ShiftMap = {};
  for (let i = 0; i < length; i += 1) {
    const from = addDays(sourceFrom, i);
    const to = addDays(targetFrom, i);
    if (source[from] && !skipDates.has(to)) result[to] = { ...source[from] };
  }
  return result;
}

export function shiftLabel(times: ShiftTimes | undefined): string {
  if (!times) return "—";
  const short = (value: string) => (value.endsWith(":00") ? String(Number(value.slice(0, 2))) : value);
  return `${short(times.start)}–${short(times.end)}`;
}

export function quarterHourOptions(): string[] {
  const options: string[] = [];
  for (let minutes = 0; minutes < 24 * 60; minutes += 15) {
    options.push(`${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`);
  }
  options.push("23:59");
  return options;
}
