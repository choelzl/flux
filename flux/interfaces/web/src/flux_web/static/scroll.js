// Save before replacing content: removing children can clamp both scroll offsets to zero.
export function scrollState(el) {
  return { top: el.scrollTop, left: el.scrollLeft,
    scrollable: el.scrollHeight > el.clientHeight + 8,
    atEnd: el.scrollTop + el.clientHeight >= el.scrollHeight - 8 };
}

export function restoreScroll(el, state, { follow = false } = {}) {
  if (!state) return;
  el.scrollLeft = state.left;
  el.scrollTop = follow && state.atEnd ? el.scrollHeight : state.top;
}
