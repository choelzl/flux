// Flux web: what this browser remembers for its viewer -- a tab chosen, the theme, a filter, a draft -- never what
// must last or be shared (the server keeps that). A browser that refuses (a private window, storage blocked)
// remembers nothing, and nothing fails for it.

/** The text kept under `key`, or `fallback`. */
export function recall(key, fallback = null) {
  try { const v = localStorage.getItem(key); return v == null ? fallback : v; } catch (_) { return fallback; }
}
/** What was kept under `key` as JSON, or `fallback` (also when it does not read). */
export function recallJson(key, fallback = null) {
  try { return JSON.parse(localStorage.getItem(key) ?? "null") ?? fallback; } catch (_) { return fallback; }
}
/** `value` kept under `key` (a string as it is, anything else as JSON); null forgets it. */
export function remember(key, value) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
  } catch (_) { /* this browser keeps nothing */ }
}
