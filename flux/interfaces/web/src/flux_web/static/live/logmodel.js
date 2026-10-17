// Flux web (D692, D697, D699, D732, D816, D1095): a loop's log as data -- its lines numbered as they come (a
// partial last line held back), each with the run's own time when the stamper wrote one; what a line is (a start's
// marker, a problem, a warning, good news); a filter (text, or /regex/). Plain functions, no page.

// D1183: a command that exited with an error, a design that never came of a draft -- the run's own failures too
const PROBLEM = /\b(error|errors|traceback|exception|failed|failure|refused|did not build|timed out|killed|no design|nothing built)\b|\bexit(?:ed| code| status)?\s+[1-9]\d*\b|✗/i;
const WARN = /\b(warning|nudged|retry|stopping|interrupted|could not)\b/i;
const GOOD = /\b(ADMITTED|DECISION|passed|decided)\b/;
const MARK = /^── started (.+?) ──$/;
const STAMP = /^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d{3} /;   // flux_web.stamp.STAMP_RE (D732)
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** D816: a line's stamp with its day -- "Oct 05 14:03:22" -- so a run of several days reads. */
export const logAt = at => `${MONTHS[Number(at.slice(5, 7)) - 1] || at.slice(5, 7)} ${at.slice(8, 10)} ${at.slice(11, 19)}`;
/** What a line is: its class in the view. */
export const lineClass = text => MARK.test(text) ? "marker" : PROBLEM.test(text) ? "bad" : WARN.test(text) ? "warn" : GOOD.test(text) ? "good" : "";
/** "problems only" keeps the problems, the warnings and where each start began. */
export const isProblem = text => PROBLEM.test(text) || WARN.test(text) || MARK.test(text);

/** A filter: text (any case) or /regex/flags; null for none; `{bad: true}` for a regex that does not compile. */
export function matcher(f) {
  f = String(f || "").trim();
  if (!f) return null;
  if (f.length > 2 && f.startsWith("/") && f.lastIndexOf("/") > 0) {
    try { return new RegExp(f.slice(1, f.lastIndexOf("/")), f.slice(f.lastIndexOf("/") + 1) || "i"); } catch (_) { return { bad: true, test: () => true }; }
  }
  const low = f.toLowerCase();
  return { test: x => x.toLowerCase().includes(low) };
}

/** The lines as they come, the last `max` kept: `add(chunk)` gives the lines it completed. */
export function logLines(max = 200000) {
  const lines = [];
  let partial = "", seen = 0;
  return {
    lines,
    add(chunk) {
      const parts = (partial + chunk).split("\n");
      partial = parts.pop();
      const fresh = parts.map(t => { const m = STAMP.exec(t); return { n: ++seen, text: m ? t.slice(m[0].length) : t, at: m ? m[1] : null }; });
      lines.push(...fresh);
      if (lines.length > max) lines.splice(0, lines.length - max);
      return fresh;
    },
    /** The rest: a log read whole ends without its last newline. */
    end() { if (!partial) return []; const t = partial; partial = ""; return this.add(t + "\n"); },
    clear() { lines.length = 0; partial = ""; seen = 0; },
  };
}

/** The lines of the latest start: from its marker on (all, when the marker is not among them). */
export function latestStart(lines) {
  for (let i = lines.length - 1; i >= 0; i--) if (MARK.test(lines[i].text)) return lines.slice(i);
  return lines;
}
