// Flux web: numbers and times said in words (D1095) -- plain functions, no page: every component says them alike.

/** When, as this browser writes a date and time. */
export const when = (t) => t ? new Date(t * 1000).toLocaleString() : "";

/** How long ago, in words: "just now", "5 min ago", "3 h ago", "2 d ago". */
export function agoText(t) {
  const s = Date.now() / 1000 - t;
  return s < 45 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
}

/** A duration: "4.2s", "3m 05s", "2h 10m". */
export function dur(s) {
  if (s == null || !isFinite(s)) return "";
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}m`;
}

/** D1130: a measured number, alike wherever it is shown (Live, Results, the charts, the Overview): `digits`
    significant digits ("12.35", "0.1235"), an integer as it is, from 1000 on two decimals at most ("1234.57"), under
    0.01 in exponent form ("1.23e-3"); never fewer digits than its integer part ("543" with 2). Empty for none. */
export function num(v, digits = 4) {
  if (v == null || v === "") return "";
  const x = Number(v);
  if (!Number.isFinite(x)) return String(v);
  if (x !== 0 && Math.abs(x) < 0.01) return x.toExponential(Math.max(0, digits - 2));
  if (Math.abs(x) >= 1000 || Number.isInteger(x)) return String(Math.round(x * 100) / 100);
  return String(Number(x.toPrecision(Math.max(digits, Math.floor(Math.log10(Math.abs(x))) + 1))));
}

/** A count of tokens: "950", "1.2k", "34k", "1.25M". */
export const fmtTok = (n) => !n ? "0" : n >= 1e9 ? (n / 1e9).toFixed(2) + "G" : n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e4 ? Math.round(n / 1e3) + "k" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(Math.round(n));

/** A size: "512 B", "1.5 KiB", "2.0 MiB". */
export const bytes = (n) => n == null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KiB` : n < 1073741824 ? `${(n / 1048576).toFixed(1)} MiB` : `${(n / 1073741824).toFixed(2)} GiB`;

/** "$1.25" -- an amount of money, `n` decimals. */
export const money = (v, n = 2) => `$${Number(v).toFixed(n)}`;

/** The seconds left to answer an agent's question `q` before it decides itself ({asked, wait_s}), or null when it
    does not wait. */
export const questionLeft = q => q?.asked && q.wait_s ? Math.max(0, Math.round(q.asked + q.wait_s - Date.now() / 1000)) : null;

/** "1 file", "3 files" (D1094: never "file(s)"); `many` where "s" is not the plural. */
export function plural(n, one, many = one + "s") { return `${n} ${n === 1 ? one : many}`; }
