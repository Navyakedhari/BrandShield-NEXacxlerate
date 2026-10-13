// Date ranges for the Reports form. Everything is computed from "today" in the viewer's zone; nothing is hard-coded.
export type RangeKey = '7d' | '30d' | 'qtd';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const pad = (n: number) => String(n).padStart(2, '0');
export const isoDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

export interface DateRange {
  from: Date;
  to: Date;
  fromIso: string;
  toIso: string;
}

export function rangeFor(key: RangeKey, now: Date = new Date()): DateRange {
  const to = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let from: Date;
  if (key === '7d') from = new Date(to.getFullYear(), to.getMonth(), to.getDate() - 6);
  else if (key === '30d') from = new Date(to.getFullYear(), to.getMonth(), to.getDate() - 29);
  else from = new Date(to.getFullYear(), Math.floor(to.getMonth() / 3) * 3, 1);
  return { from, to, fromIso: isoDay(from), toIso: isoDay(to) };
}

/** "6 to 12 Oct 2026" or "13 Sep to 12 Oct 2026" or "1 Jul to 12 Oct 2026". */
export function rangeText(r: { from: Date; to: Date }): string {
  const f = r.from;
  const t = r.to;
  const tail = `${t.getDate()} ${MONTHS[t.getMonth()]} ${t.getFullYear()}`;
  if (f.getMonth() === t.getMonth() && f.getFullYear() === t.getFullYear()) return `${f.getDate()} to ${tail}`;
  return `${f.getDate()} ${MONTHS[f.getMonth()]}${f.getFullYear() !== t.getFullYear() ? ` ${f.getFullYear()}` : ''} to ${tail}`;
}

export function rangeLabel(key: RangeKey, now: Date = new Date()): string {
  if (key === '7d') return `Last 7 days (${rangeText(rangeFor('7d', now))})`;
  if (key === '30d') return 'Last 30 days';
  return 'Quarter to date';
}

/** Start of the range day (local) and end of the range day, as epoch ms, to filter `found_at`. */
export function bounds(r: DateRange): { start: number; end: number } {
  return { start: r.from.getTime(), end: new Date(r.to.getFullYear(), r.to.getMonth(), r.to.getDate() + 1).getTime() - 1 };
}
