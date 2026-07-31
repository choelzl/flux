# flows/ — CHIA nodes, MCP tools, CLI

One definition, three surfaces: every capability is a typed Python function, a CHIA
`@ChiaFunction` node, and an MCP tool (fastmcp), generated together. Includes the independent
validity checker and holdout-corpus enforcement that gate generation/.

See [docs/04.md §7](../docs/04.md#7-l6--flows-and-the-agent-surface).

`cli/` is implemented: `flux import` / `flux eval` / `flux replay`, a real installable console
script (`flux-cli` package). Hand-written argparse, not generated from a shared decorator — see
its README for why that's a deliberate, real stepping stone rather than the eventual
one-definition-three-surfaces shape. `chia_nodes/` and `mcp/` are both still empty.
