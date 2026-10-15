// Live (D1095): the task model it reads -- the journal's events and the live state, applied as looptree.js
// builds them (D752), from the loop's one stream (D917) or a past start's retained journal.

const WINDOW = 30;                 // D759: a day-long start opens on its last passes; "Earlier passes" loads the rest

/** `port.on(kind, callbacks, options)`: the stream's (store.js). Returns the model, the run's end as the page
    knows it (D928: a journal cut short has its open tasks interrupted), and the earlier passes to load. */
export function journal(port) {
  const LT = window.FluxLoopTree, model = LT.model();
  let lastLive = null, endedAt = null;
  const onEvent = e => {
    LT.apply(model, e);
    // D918: a live snapshot that came before its task
    if (e.ev === "start" && lastLive?.updates?.[e.id]) { const n = model.nodes.get(e.id); if (n && n.t1 == null) n.fields = lastLive.updates[e.id]; }
  };
  port.on("events", { onData: onEvent, onState: () => { if (lastLive) LT.applyLive(model, lastLive); } }, { window: WINDOW });
  // D761: what runs now -- its live fields -- from live.json, whole each time it changes
  port.on("live", { onData: doc => { lastLive = doc; LT.applyLive(model, doc); } });
  return {
    model,
    onEvent,
    ended(t) { endedAt = t ?? null; },
    settle() { if (endedAt != null || model.settled) LT.settle(model, endedAt); },
  };
}
