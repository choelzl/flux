// Flux web: the charts and marks drawn as SVG (D889: split out of app.js; D1095: the charts are components, in
// charts/ -- here they are drawn into an element, for a page built with h() and for the tests that read one).

import { html, render } from "./view.js";
import { designPoints, directionOf, groupList, groupStyles, scopesOf } from "./chartdata.js";
import { Legend, num4 } from "./charts/marks.js";
import { BestChart } from "./charts/BestChart.js";
import { ParetoChart } from "./charts/ParetoChart.js";
import { TimeChart } from "./charts/TimeChart.js";

// ================================================================ charts (D692)
const SVGNS = "http://www.w3.org/2000/svg";
function sv(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== "") el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
/** The Flux mark (D696): a chip whose pins and body follow the theme, the orange trace its "f". */
function logo(size = 22) {
  const pins = [[78, 8, 16, 28], [120, 8, 16, 28], [162, 8, 16, 28], [120, 220, 16, 28], [162, 220, 16, 28], [8, 78, 28, 16], [8, 120, 28, 16], [8, 162, 28, 16],
    [220, 78, 28, 16], [220, 120, 28, 16], [220, 162, 28, 16]];
  return sv("svg", { viewBox: "0 0 256 256", width: size, height: size, class: "logo", "aria-hidden": "true" },
    sv("g", { class: "lg-g" }, pins.map(([x, y, w, hh]) => sv("rect", { x, y, width: w, height: hh, rx: 8 }))),
    sv("rect", { x: 78, y: 208, width: 16, height: 40, rx: 8, class: "lg-o" }),
    sv("rect", { x: 40, y: 40, width: 176, height: 176, rx: 32, class: "lg-gs" }),
    sv("path", { d: "M86 248V118A32 32 0 0 1 118 86H174", class: "lg-os" }),
    sv("path", { d: "M128 248V146A18 18 0 0 1 146 128H166", class: "lg-gs" }));
}
function bellIcon() {
  return sv("svg", { viewBox: "0 0 24 24", width: 18, height: 18, class: "icon", "aria-hidden": "true" },
    sv("path", { d: "M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z", fill: "none", stroke: "currentColor", "stroke-width": 1.7, "stroke-linejoin": "round" }),
    sv("path", { d: "M10 20.5a2 2 0 0 0 4 0", fill: "none", stroke: "currentColor", "stroke-width": 1.7, "stroke-linecap": "round" }));
}
/** A component drawn into an element of its own (still live: a time chart's pointer follows it). */
const drawn = vnode => { const box = document.createElement("div"); render(vnode, box); return box.firstElementChild || box; };
const bestChart = (rows, obj, passes, opts = {}) => drawn(html`<${BestChart} rows=${rows} obj=${obj} passes=${passes} ...${opts} />`);
const paretoChart = (designs, xm, ym, stage, objectives, onPick, opts = {}) =>
  drawn(html`<${ParetoChart} designs=${designs} xm=${xm} ym=${ym} stage=${stage} objectives=${objectives} onPick=${onPick} ...${opts} />`);
const timeChart = (samples, series, opts = {}) => drawn(html`<${TimeChart} samples=${samples} series=${series} ...${opts} />`);
const legend = (S, opts = {}) => drawn(html`<${Legend} S=${S} ...${opts} />`);

export { bellIcon, bestChart, designPoints, directionOf, groupList, groupStyles, legend, logo, num4, paretoChart, scopesOf, sv, timeChart };
