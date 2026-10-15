// Flux web: the charts and marks drawn as SVG (D889: split out of app.js; D1095: the charts are components, in
// charts/ -- here two are drawn into an element, for the tests that read one).

import { drawn, html } from "./view.js";
import { designPoints, directionOf, groupList, groupStyles, scopesOf } from "./chartdata.js";
import { num4 } from "./charts/marks.js";
import { BestChart } from "./charts/BestChart.js";
import { ParetoChart } from "./charts/ParetoChart.js";

const bestChart = (rows, obj, passes, opts = {}) => drawn(html`<${BestChart} rows=${rows} obj=${obj} passes=${passes} ...${opts} />`);
const paretoChart = (designs, xm, ym, stage, objectives, onPick, opts = {}) =>
  drawn(html`<${ParetoChart} designs=${designs} xm=${xm} ym=${ym} stage=${stage} objectives=${objectives} onPick=${onPick} ...${opts} />`);

export { bestChart, designPoints, directionOf, groupList, groupStyles, num4, paretoChart, scopesOf };
