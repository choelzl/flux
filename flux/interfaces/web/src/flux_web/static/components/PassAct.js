// Flux web (D1006): Stop or Restart, after this pass -- pressed once it is asked: the button says so ("Stopping…",
// the small spinner, no red: nothing is wrong); pressed again (confirmed, a red button in that dialog) it abandons
// the pass. The loop's head and the loops list alike.

import { cx, html } from "../view.js";
import { Act } from "./Act.js";

const ING = { Stop: "Stopping…", Restart: "Restarting…" };

/** `what` "Stop" or "Restart"; `asked` true once it is scheduled; `small` in a list's row. */
export const PassAct = ({ what, asked, onClick, small = false }) => html`<${Act} cls=${cx(small && "small", asked && "asked")}
  title=${asked ? `${what} scheduled after this pass: press again to abandon the pass` : `${what} after this pass`} onClick=${onClick}>${asked ? ING[what] : what}<//>`;
