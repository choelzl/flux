# interfaces/mcp/ — every node as an MCP tool

`flux_mcp.FluxTool` is a `chia.base.tools.ChiaTool` subclass (CHIA's own base class for MCP
tool servers deployed onto Ray workers, the one `BashTool` and `ChiaToolTemplate` use). Its
`setup()` registers every public method of the class as a tool named `flux_<method>`
(`FluxTool.tools()`), so a method IS a tool and cannot be left unregistered (D452); each method
is a thin wrapper that calls the matching `flux_chia_nodes` function in-process (the MCP call is
already the network hop) and serialises the result to a JSON-safe shape (`.to_dict()` for a
`Result` or a report). The method's docstring is the tool's agent-facing documentation and the
`Args:` block the MCP schema is built from, which is why the nodes are not registered directly:
the node is typed Python, the tool is documented for a model.

```python
import ray
from flux_mcp import FluxTool

ray.init()
tool = FluxTool("flux")     # a Ray-actor-backed uvicorn server at http://{tool.hostname}:{tool.port}/flux/mcp
tool.stop()
```

Parity with the node package's `__all__` and with the table in
[docs/agent-surface.md](../../../docs/agent-surface.md) is guarded in both directions by
`tests/unit/test_mcp_surface_parity.py`; the site's catalog pages are generated from
`setup()`'s registrations (`../website/generate_catalog.py`, D387) and `flux_omni` reads the
same registrations to build the catalog its model plans over (omni never offers itself).
`tests/integration/test_flux_mcp_tool_live.py` is a client round trip
(`mcp.ClientSession` + `streamable_http_client`) against the surface.

Two FastMCP gotchas found by inspection: a bare-`dict`-returning tool's result lands directly
in `structuredContent` with no wrapper key, while a `list[...]` or `X | None` return is wrapped
in a `{"result": ...}` envelope.
