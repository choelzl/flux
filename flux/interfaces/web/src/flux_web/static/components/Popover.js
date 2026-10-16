// Flux web (D1149): what opens over the page -- the bell's list, the account menu on a phone, a file's Actions -- open
// or not, closed by a click outside it or Escape (the focus back on its opener when it was inside).

import { useEffect, useRef, useState } from "../view.js";

/** {open, setOpen, ref}: `ref` on the element a click may land in and keep it open (its opener with it), or
    `inside(target)` to say so otherwise. */
export function usePopover({ inside = null } = {}) {
  const [open, setOpen] = useState(false), ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const within = t => inside ? inside(t) : !!ref.current?.contains(t);
    const click = e => { if (!within(e.target)) setOpen(false); };
    const key = e => {
      if (e.key !== "Escape") return;
      const el = ref.current, back = !!el?.contains(document.activeElement);
      setOpen(false);
      if (back) el.querySelector("summary, button")?.focus();
    };
    document.addEventListener("click", click); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("click", click); document.removeEventListener("keydown", key); };
  }, [open]);
  return { open, setOpen, ref };
}
