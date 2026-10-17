// Flux web, configure (D1118, D1146): who works each step of the loop, as the Graph says it -- each box's kinds of
// worker (rules, a model, the coding agent, a script, off), the one its value is, and the value a kind picks. The
// model says them (crafter-model.js whoOptions, the docs' drawing's too); here they take the model as `M`.

export const isAgent = v => typeof v === "string" && v.startsWith("agent:");
/** An agent's name as a box says it ("own settings" for one written with its own, D728). */
export const agentWord = (M, v) => M.agentWord(v);

/** A box's kinds of worker, for its current value `cur` and the agent the Graph names; `searchOnly` Design under a
    parameter-only search (off unless a script or files). */
export const whoOptions = (M, box, cur, agent, searchOnly = false) => M.whoOptions(box, cur, agent, searchOnly);
/** The kind `cur` is among `options`. */
export const whoOn = (options, cur) => (options.find(o => o.is(cur)) || options[0] || {}).k;

/** The agent the Graph names: the first a box is given, else claude (or the first there is). */
export const agentOf = (M, state) => M.agentOf(state);
