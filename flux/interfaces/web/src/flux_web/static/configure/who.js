// Flux web, configure (D1118): who works each step of the loop, as the Graph says it -- each box's kinds of worker
// (rules, a model, the coding agent, a script, off), the one its value is, and the value a kind picks. A script works a
// step only where Flux takes one: Design (`generate: {command}` or `{catalog}`) and Orchestrate (a search `command`).

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
    case "orchestrate": return [opt("rules", "rules", "rules"), modelOf(["default", "model", "tools"]), a, opt("command", "script", "script")];
    case "generate": return [opt("model", "model", "model"), a, opt("command", "script", "script"), opt("catalog", "files", "script")];
    case "critique": return [opt("off", "off", "off"), opt("model", "model", "model"), a];
    case "calibrate": return [opt("on", "on", "rules"), opt("off", "off", "off")];
    case "select": return [opt("objectives", "goals", "rules"), a];
    case "knowledge": return [opt("default", "library", "rules"), opt("none", "off", "off")];
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

/** Who does the work, in one word for the whole loop: no AI, a model, or a coding agent writes the designs. */
export function workOf(M, state) {
  const f = state.flow || {};
  if (isAgent(f.generate)) return "agent";
  const crit = Object.values(M.criticsOf(state)).every(v => v === "off");
  const quiet = ["validate", "orchestrate", "plan", "select", "lessons"].every(b => ["rules", "off", "command", "objectives", "mined"].includes(f[b]));
  return ["command", "catalog"].includes(f.generate) && f.knowledge === "none" && crit && quiet ? "none" : "model";
}

/** The whole loop set to one way of working: rules and scripts only, a model's defaults, or the agent designing. */
export function setWork(M, state, way, agent) {
  const f = state.flow, raw = state.agentRaw || {};
  const drop = b => { delete raw[b]; };
  if (way === "none") {
    for (const b of ["validate", "plan", "select", "lessons", "orchestrate"]) {
      const to = { validate: "rules", plan: "off", select: "objectives", lessons: isAgent(f.lessons) ? "mined" : f.lessons, orchestrate: f.orchestrate === "command" ? "command" : "rules" }[b];
      if (f[b] !== to) { f[b] = to; drop(b); }
    }
    if (M.dseGroup(f.dse) === "delegated") { f.dse = "none"; drop("dse"); }
    if (!["command", "catalog"].includes(f.generate)) { f.generate = "command"; drop("generate"); }
    f.knowledge = "none";
    for (const b of M.CRITIC_BOXES) M.setCritic(state, b, "off");
  } else {
    if (["command", "catalog"].includes(f.generate) || (way === "model" && isAgent(f.generate))) { f.generate = "model"; drop("generate"); }
    if (way === "agent") { f.generate = `agent:${agent}`; drop("generate"); }
    if (f.orchestrate === "rules") f.orchestrate = "default";
    if (f.knowledge === "none") f.knowledge = "default";
  }
}

/** Every box on an agent, the critics too, follows the agent the Graph names (not one with its own settings). */
export function renameAgent(M, state, agent) {
  for (const b of Object.keys(state.flow)) if (isAgent(state.flow[b]) && state.flow[b] !== M.INLINE_AGENT) state.flow[b] = `agent:${agent}`;
  const c = M.criticsOf(state);
  for (const b of M.CRITIC_BOXES) if (isAgent(c[b]) && c[b] !== M.INLINE_AGENT) M.setCritic(state, b, `agent:${agent}`);
}
