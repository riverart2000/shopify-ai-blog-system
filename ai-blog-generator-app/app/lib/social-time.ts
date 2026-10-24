/**
 * Store-timezone date helpers (no dependencies; Intl only). Client- and server-safe.
 *
 * Epochs are seconds. "Date keys" are plain calendar dates "YYYY-MM-DD"; date
 * arithmetic on keys uses UTC so it is unaffected by daylight-saving changes.
 */

export const DEFAULT_TIMEZONE = "Europe/London";
const LOCALE = "en-GB";

export function isValidTimezone(tz: string | undefined | null): boolean {
  if (!tz) return false;
  try {
    new Intl.DateTimeFormat(LOCALE, { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

export function safeTimezone(tz: string | undefined | null): string {
  return isValidTimezone(tz) ? String(tz) : "UTC";
}

const partFormatters = new Map<string, Intl.DateTimeFormat>();

function partsFormatter(tz: string): Intl.DateTimeFormat {
  let formatter = partFormatters.get(tz);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
    partFormatters.set(tz, formatter);
  }
  return formatter;
}

type ZonedParts = { year: number; month: number; day: number; hour: number; minute: number; second: number };

export function zonedParts(epochMs: number, tz: string): ZonedParts {
  const parts = partsFormatter(safeTimezone(tz)).formatToParts(new Date(epochMs));
  const get = (type: string) => Number(parts.find((part) => part.type === type)?.value || 0);
  return {
    year: get("year"), month: get("month"), day: get("day"),
    hour: get("hour") % 24, minute: get("minute"), second: get("second"),
  };
}

function offsetMs(epochMs: number, tz: string): number {
  const p = zonedParts(epochMs, tz);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return asUtc - Math.floor(epochMs / 1000) * 1000;
}

const pad = (value: number) => String(value).padStart(2, "0");

/** "YYYY-MM-DDTHH:mm" (or "YYYY-MM-DD") in `tz` → epoch seconds. Null when unparseable. */
export function zonedLocalToEpoch(local: string, tz: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec((local || "").trim());
  if (!match) return null;
  const [, y, m, d, hh = "00", mm = "00"] = match;
  const guess = Date.UTC(Number(y), Number(m) - 1, Number(d), Number(hh), Number(mm));
  const zone = safeTimezone(tz);
  const first = offsetMs(guess, zone);
  let epoch = guess - first;
  const second = offsetMs(epoch, zone);
  if (second !== first) epoch = guess - second;
  return Math.floor(epoch / 1000);
}

/** Epoch seconds → "YYYY-MM-DDTHH:mm" in `tz` (for <input type="datetime-local">). */
export function epochToZonedInput(epoch: number | null | undefined, tz: string): string {
  if (!epoch) return "";
  const p = zonedParts(epoch * 1000, tz);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
}

export function zonedDateKey(epoch: number, tz: string): string {
  const p = zonedParts(epoch * 1000, tz);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}

export function todayKey(tz: string): string {
  return zonedDateKey(Math.floor(Date.now() / 1000), tz);
}

function keyToUtc(key: string): Date {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(Date.UTC(y || 1970, (m || 1) - 1, d || 1));
}

function utcToKey(date: Date): string {
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

export function isDateKey(value: string | null | undefined): value is string {
  return Boolean(value && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(keyToUtc(value).getTime()));
}

export function addDays(key: string, days: number): string {
  const date = keyToUtc(key);
  date.setUTCDate(date.getUTCDate() + days);
  return utcToKey(date);
}

export function addMonths(key: string, months: number): string {
  const date = keyToUtc(key);
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() + months);
  return utcToKey(date);
}

/** Monday = 0 … Sunday = 6 (matches Python's weekday()). */
export function weekdayOfKey(key: string): number {
  return (keyToUtc(key).getUTCDay() + 6) % 7;
}

export function startOfWeek(key: string): string {
  return addDays(key, -weekdayOfKey(key));
}

export function startOfMonth(key: string): string {
  return `${key.slice(0, 7)}-01`;
}

/** Epoch seconds of 00:00 UTC on a date key (for coarse API ranges). */
export function keyToUtcEpoch(key: string): number {
  return Math.floor(keyToUtc(key).getTime() / 1000);
}

export function formatDateKey(key: string, options: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" }): string {
  return new Intl.DateTimeFormat(LOCALE, { ...options, timeZone: "UTC" }).format(keyToUtc(key));
}

export function formatDateTime(
  epoch: number | null | undefined,
  tz: string,
  options: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" },
): string {
  if (!epoch) return "—";
  return new Intl.DateTimeFormat(LOCALE, { ...options, timeZone: safeTimezone(tz) }).format(new Date(epoch * 1000));
}

export function formatTime(epoch: number | null | undefined, tz: string): string {
  return formatDateTime(epoch, tz, { hour: "2-digit", minute: "2-digit" });
}

export function formatFull(epoch: number | null | undefined, tz: string): string {
  return formatDateTime(epoch, tz, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function relativeTime(epoch: number | null | undefined, nowSeconds = Math.floor(Date.now() / 1000)): string {
  if (!epoch) return "never";
  const diff = epoch - nowSeconds;
  const abs = Math.abs(diff);
  const unit = abs < 60 ? ["second", 1] : abs < 3600 ? ["minute", 60] : abs < 86400 ? ["hour", 3600] : ["day", 86400];
  const value = Math.round(diff / (unit[1] as number));
  return new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" }).format(value, unit[0] as Intl.RelativeTimeFormatUnit);
}

export function timezoneLabel(tz: string): string {
  try {
    const name = new Intl.DateTimeFormat(LOCALE, { timeZone: safeTimezone(tz), timeZoneName: "short" })
      .formatToParts(new Date())
      .find((part) => part.type === "timeZoneName")?.value;
    return name ? `${tz} (${name})` : tz;
  } catch {
    return tz;
  }
}
