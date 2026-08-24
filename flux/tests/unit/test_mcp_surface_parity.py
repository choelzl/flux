"""The "one definition, three surfaces" convention (docs/agent-surface.md), enforced mechanically
instead of by memory (docs/decisions.md D119).

Every capability here is meant to be a typed Python function, a `@ChiaFunction()` node, and an MCP
tool. The first two are the same object by construction, so nothing can drift between them — and
since D452 the tool surface is derived from `FluxTool`'s own methods rather than a second
hand-kept list of the same names, so a tool cannot be registered without a method or a method
left unregistered. What can still drift is what an agent is TOLD exists: the node package's
`__all__`, and `docs/agent-surface.md`'s table. Those are what these guards compare.

The live MCP test that would have caught the original class of mistake runs only in
`tests/integration/`, which the standard regression command does not include; it had been failing
unnoticed across four separate additions. This file is the version that runs on every change: no
server, no Ray, no network.
"""

from __future__ import annotations

import inspect
import re


def _registered_tool_suffixes() -> set[str]:
    """The tool list, ASKED of the class rather than regexed out of `setup`'s source (D452)."""
    from flux_mcp.tool import FluxTool

    return set(FluxTool.tools())


def _exported_node_names() -> set[str]:
    import flux_chia_nodes

    return {n[len("flux_"):] for n in flux_chia_nodes.__all__ if n.startswith("flux_")}


def test_every_chia_node_is_reachable_as_an_mcp_tool():
    """The direction that actually bites: a capability an agent cannot call is not on the agent
    surface, however real the Python function behind it is."""
    missing = _exported_node_names() - _registered_tool_suffixes()
    assert not missing, (
        f"exported CHIA nodes with no MCP registration: {sorted(missing)} — add a documented "
        "public `def <name>(...)` method to FluxTool; `setup()` registers every one it finds"
    )


def test_every_mcp_tool_is_backed_by_an_exported_node():
    """The other direction, which catches a rename that left the old registration behind."""
    orphaned = _registered_tool_suffixes() - _exported_node_names()
    assert not orphaned, f"MCP tools with no matching exported CHIA node: {sorted(orphaned)}"


def test_each_registered_tool_has_a_real_method_with_a_docstring():
    """An MCP tool's docstring IS its agent-facing description: the description a model reads
    and, for 49 of these tools, the per-argument documentation the tool schema is built from.
    That is why the methods exist at all rather than the nodes being registered directly —
    they are the surface's documentation, not pass-throughs (D452)."""
    from flux_mcp.tool import FluxTool

    for suffix in sorted(_registered_tool_suffixes()):
        method = getattr(FluxTool, suffix, None)
        assert callable(method), f"FluxTool registers {suffix!r} but has no such method"
        assert (method.__doc__ or "").strip(), f"FluxTool.{suffix} has no docstring for the agent"


def test_the_tool_list_is_derived_and_cannot_drift_back_into_a_second_copy():
    """`setup()` used to carry 61 `add_tool` lines naming the same 61 methods — one list to
    keep in step with another, which is the drift this file exists for. It is one loop now,
    and this fails if a hand-written registration list comes back."""
    from flux_mcp.tool import FluxTool

    source = inspect.getsource(FluxTool.setup)
    assert source.count("add_tool") == 1, (
        "FluxTool.setup() must register the tools in one loop over `tools()`, not name them")
    assert len(_registered_tool_suffixes()) >= 40, "guards the guard: the list must not be empty"


def test_every_node_the_package_imports_is_exported():
    """`__all__` is what the MCP layer and the docs guards read to learn what exists, so a node
    imported into the package and left out of it is invisible to every check above."""
    import flux_chia_nodes

    imported = {n for n in vars(flux_chia_nodes)
                if n.startswith("flux_") and callable(getattr(flux_chia_nodes, n))}
    missing = imported - set(flux_chia_nodes.__all__)
    assert not missing, f"nodes imported but not exported in __all__: {sorted(missing)}"


def test_positionally_forwarded_tools_pass_their_arguments_in_the_nodes_order():
    """An MCP method that forwards positionally to its node — `return flux_x(a, b, c)` — is
    silently wrong if the two signatures ever disagree on order. Nothing raises: the values just
    land in the wrong parameters, and for same-typed neighbours like `protocol_id, role` or
    `workload, arch` the result is a plausible answer to a question nobody asked.

    Checked mechanically rather than by eye, and the count is asserted so a future refactor that
    switches everything to keywords fails this loudly instead of passing vacuously
    (docs/decisions.md D190).
    """
    from flux_mcp import tool as tool_module
    from flux_mcp.tool import FluxTool

    forwarded, mismatches = 0, []
    for name, method in inspect.getmembers(FluxTool, inspect.isfunction):
        if name.startswith("_") or name == "setup":
            continue
        body = inspect.getsource(method)
        match = re.search(r"return\s+(flux_\w+)\(", body)
        if match is None:
            continue
        node = getattr(tool_module, match.group(1), None)
        target = getattr(node, "__wrapped__", None) or node
        try:
            node_params = list(inspect.signature(target).parameters)
        except (TypeError, ValueError):
            continue

        call = body[match.end():]
        depth, i = 1, 0
        while i < len(call) and depth > 0:
            depth += (call[i] == "(") - (call[i] == ")")
            i += 1
        positional = [a.strip() for a in call[:i - 1].split(",") if a.strip() and "=" not in a]
        if not positional:
            continue

        forwarded += 1
        method_params = [p for p in inspect.signature(method).parameters if p != "self"]
        if method_params[:len(positional)] != node_params[:len(positional)]:
            mismatches.append(
                f"{name} -> {match.group(1)}: tool has {method_params[:len(positional)]}, "
                f"node has {node_params[:len(positional)]}"
            )

    assert forwarded >= 5, (
        f"only {forwarded} tools forward positionally — if that is a deliberate refactor to "
        "keyword arguments, lower this bound; otherwise this check has stopped seeing anything"
    )
    assert not mismatches, "positional forwarding in the wrong order:\n" + "\n".join(mismatches)


def test_every_registered_tool_appears_in_the_agent_surface_table():
    """`docs/agent-surface.md`'s table is the human-facing list of what an agent can call, and
    docs/decisions.md D196 removed the tool *counts* from the living docs on the grounds that this
    table is the authoritative list. That claim is only worth making if the table is complete —
    when D196 made it, the four protocol tools (D174/D178) were missing from it (D197).

    Rows may name several tools at once (`flux_get_result` / `flux_find_results` share one), so
    every backticked name in a row counts, not just the first — scanning only the first is how a
    documented tool looked missing on the first attempt at this check.
    """
    from pathlib import Path

    from flux_mcp.tool import FluxTool

    registered = set(FluxTool.tools())
    table = (Path(__file__).resolve().parents[2] / "../docs/agent-surface.md").resolve().read_text()
    documented = {
        name
        for line in table.splitlines() if line.startswith("| `flux_")
        for name in re.findall(r"`flux_(\w+)`", line.split("|")[1])
    }

    assert len(registered) >= 40, "guards the guard: the registry scan must not come back empty"
    assert not registered - documented, (
        f"tools missing from docs/agent-surface.md's table: {sorted(registered - documented)}"
    )
    assert not documented - registered, (
        f"table rows with no registered tool: {sorted(documented - registered)}"
    )
