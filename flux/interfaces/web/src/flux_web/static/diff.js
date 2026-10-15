// Flux web (D693, D1095): two texts compared, line by line -- plain functions, no page (the configurator's saves, an
// agent's revision, two designs' sources).

/** A line diff (D693): the longest common subsequence of lines, as [op, text] with op " ", "-"
    or "+". The ends every document shares are cut off before the table, so it stays small. */
export function lineDiff(a, b) {
  const A = String(a).split("\n"), B = String(b).split("\n");
  let s = 0; while (s < A.length && s < B.length && A[s] === B[s]) s++;
  let e = 0; while (e < A.length - s && e < B.length - s && A[A.length - 1 - e] === B[B.length - 1 - e]) e++;
  const a2 = A.slice(s, A.length - e), b2 = B.slice(s, B.length - e), n = a2.length, m = b2.length;
  const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = a2[i] === b2[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const mid = []; let i = 0, j = 0;
  while (i < n && j < m) {
    if (a2[i] === b2[j]) { mid.push([" ", a2[i]]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) mid.push(["-", a2[i++]]);
    else mid.push(["+", b2[j++]]);
  }
  while (i < n) mid.push(["-", a2[i++]]);
  while (j < m) mid.push(["+", b2[j++]]);
  return [...A.slice(0, s).map(t => [" ", t]), ...mid, ...A.slice(A.length - e).map(t => [" ", t])];
}
/** The diff's rows to show: each change with `context` lines around it, the rest folded -- {fold: n} or
    {op, text, a, b} (the line's number before and after; none on the side it is not). */
export function diffRows(ops, context = 3) {
  const keep = ops.map(() => false), rows = [];
  ops.forEach((o, k) => { if (o[0] !== " ") for (let d = -context; d <= context; d++) if (ops[k + d]) keep[k + d] = true; });
  let a = 0, b = 0, folded = 0;
  ops.forEach(([op, text], k) => {
    if (op !== "+") a++;
    if (op !== "-") b++;
    if (!keep[k]) { folded++; return; }
    if (folded) { rows.push({ fold: folded }); folded = 0; }
    rows.push({ op, text, a: op === "+" ? "" : String(a), b: op === "-" ? "" : String(b) });
  });
  if (folded) rows.push({ fold: folded });
  return rows;
}
/** How many lines were added and removed. */
export const diffCounts = ops => ({ added: ops.filter(o => o[0] === "+").length, removed: ops.filter(o => o[0] === "-").length });
