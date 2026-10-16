// Flux web, configure (D1118): who works each step of the loop, as the Graph says it -- each box's kinds of worker
// (rules, a model, the coding agent, a script, off), the one its value is, and the value a kind picks. A script works a
// step only where Flux takes one: Design (`generate: {command}` or `{catalog}`) and Orchestrate (a search `command`).
// The Library (knowledge) is on or off; who sums up its papers (digest) and its lessons are chosen inside it.

export const isAgent = v => typeof v === "string" && v.startsWith("agent:");
/** An agent's name as a box says it ("own settings" for one written with its own, D728). */
export const agentWord = (M, v) => v === M.INLINE_AGENT ? "agent, own settings" : String(v).slice(6);

const opt = (k, label, cls, value = k) => ({ k, label, cls, is: v => v === value, value: () => value });

/** A box's kinds of worker, for its current value `cur` and the agent the Graph names. */
export function whoOptions(M, box, cur, agent) {
  const a = { k: "agent", cls: "agent", label: isAgent(cur) ? agentWord(M, cur) : agent, is: isAgent, value: () => isAgent(cur) ? cur : `agent:${agent}` };
  const modelOf = vals => ({ k: "model", label: "model", cls: "model", is: v => vals.includes(v), value: () => vals.includes(cur) ? cur : vals[0] });
  switch (box) {
    case "validate": return [opt("rules", "rules", "rules"), opt("model", "model", "model"), a];
    case "plan": return [opt("off", "off", "off"), opt("model", "model", "model"), a];
    case "feedback": return [opt("human", "notes", "rules"), opt("off", "off", "off")];
    // D1133: no `orchestrate:` line is Flux's default -- rules pick the kind of work (a model picks the part when there are parts)
    case "orchestrate": return [opt("default", "default", "rules"), opt("rules", "rules", "rules"), modelOf(["model", "tools"]), a, opt("command", "script", "script")];
    case "generate": return [opt("model", "model", "model"), a, opt("command", "script", "script"), opt("catalog", "files", "script")];
    case "critique": return [opt("off", "off", "off"), opt("model", "model", "model"), a];
    case "calibrate": return [opt("on", "on", "rules"), opt("off", "off", "off")];
    case "select": return [opt("objectives", "goals", "rules"), a];
    case "knowledge": return [opt("default", "on", "rules"), opt("none", "off", "off")];
    case "digest": return [opt("model", "model", "model"), a];
    case "lessons": return [opt("off", "off", "off"), opt("mined", "mined", "rules"), a];
    default: return [];
  }
}
/** The kind `cur` is among `options`. */
export const whoOn = (options, cur) => (options.find(o => o.is(cur)) || options[0] || {}).k;

/** The agent the Graph names: the first a box is given, else claude (or the first there is). */
export function agentOf(M, state) {
  const vals = [...Object.values(state.flow || {}), ...Object.values(M.criticsOf(state))];
  const found = vals.find(v => isAgent(v) && v !== M.INLINE_AGENT);
  return found ? found.slice(6) : M.AGENTS.includes("claude") ? "claude" : M.AGENTS[0];
}
