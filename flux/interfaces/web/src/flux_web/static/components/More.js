// Flux web: "show more" -- one look wherever a list holds some of its lines back (earlier tries, earlier tasks, earlier
// lines, the next page of a table): a small accent link, its words the count it shows ("▸ 31 earlier tries").

import { cx, html } from "../view.js";

/** `children` what pressing it shows; `cls` its place (an indent); any other attribute on the button. */
export const More = ({ onClick, cls = "", children, ...rest }) => html`<button ...${rest} type="button" class=${cx("show-more", cls)} onClick=${onClick}>${children}</button>`;
