// Shared timeline colours: work keeps its colour, agent activity is an overlay.
export const WORK_COLORS = { setup: "#8f9aa6", plan: "#c98a56", search: "#d9b440", design: "#5b8def",
  check: "#4fb286", measure: "#48b3c9", choose: "#e8804f", critic: "#a3c956" };
export const AGENT_COLOR = "#d45eae";
export const workLabel = kind => kind[0].toUpperCase() + kind.slice(1);
