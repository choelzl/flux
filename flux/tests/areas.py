"""The test areas (D1171): which product files each area covers, its unit test files, its e2e steps --
so `tests/check.py --area live` or `--changed` runs a part of the suite, not all of it.

Paths are globs relative to flux/ (`../` for the repository's own files, as `git` names them from
flux/): `*` within a folder, `**` across folders. A file may sit in several areas. A change to a
`SHARED` file -- what every page or every test leans on -- or to a file no area's `sources` match,
runs everything.

An area's e2e steps are its module's, `tests/e2e/steps/<area>.py` (a step lives in one module), and
the ones its `e2e` names from other modules that also walk it. `tests/unit/test_areas.py` keeps
the map whole: every unit test file and every step in an area, nothing named that does not exist.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

FLUX = Path(__file__).resolve().parents[1]
W = "interfaces/web/src/flux_web/"
S = W + "static/"
L = "core/loop/src/flux_loop/"
CLI = "interfaces/cli/src/flux_cli/"
K = "mentor/knowledge/src/flux_knowledge/"


def _loop(*names: str) -> list[str]:
    return [f"{L}{n}.py" for n in names]


#: a change here runs everything
SHARED = [
    S + "view.js", S + "ui.js", S + "state.js", S + "flux.css", S + "components/**", S + "app.js", S + "index.html",
    S + "vendor/**", S + "format.js", S + "stored.js", S + "sorting.js",
    W + "app.py", W + "store.py", W + "runs.py", W + "workspace.py", W + "__init__.py",
    L + "document/spec.py", L + "__init__.py",
    "tests/check.py", "tests/areas.py", "tests/conftest.py", "tests/loop_fixtures.py", "tests/fixtures/**",
    "tests/unit/conftest.py", "tests/unit/toy_document.py", "tests/unit/web_agents.py", "tests/unit/fixtures/**",
    "tests/e2e/harness.py", "tests/e2e/web_ui.py", "tests/e2e/steps/__init__.py",
    "flake.nix", "flake.lock", "pyproject.toml", "pytest.ini", "*/pyproject.toml", "**/pyproject.toml",
]

#: files no test reads (outside flux/, every file no area names is one too): a change there runs nothing
UNTESTED = ["scripts/**", "tests/unit/*.txt"]

AREAS: dict[str, dict[str, list[str]]] = {
    "live": {
        "sources": [S + "live/**", W + "timeline.py", W + "run_history.py", *_loop("journal", "observe"), "tests/unit/live_js.py"],
        "unit": ["test_live_*.py", "test_web_d917.py", "test_web_d918.py", "test_web_run_history.py", "test_empty_starts.py",
                 "test_work_style.py", "test_web_time.py", "test_web_stamp.py", "test_journal.py", "test_agent_live.py",
                 "test_web_document_selection.py"],
        "e2e": ["keyboard journey", "the decision and the best"],
    },
    "configurator": {
        "sources": [S + "configure/**", S + "configure.js", W + "configure.py", "../website/docs/assets/crafter-model.js"],
        "unit": ["test_configurator.py", "test_loop_crafter.py", "test_web_d905.py", "test_web_document_refused.py",
                 "test_docs_crafter.py"],
        "e2e": ["hidden in a new loop", "docs crafter", "error feedback", "author refused", "new loop by agent"],
    },
    "author": {
        "sources": [S + "configure/byagent/**", W + "authoring.py", L + "author.py", L + "author_reference.md",
                    "../skills/flux-loop-creator/**", CLI + "commands.py"],
        "unit": ["test_author*.py", "test_web_author*.py", "test_golden_prototype.py"],
        "e2e": ["hidden to the author"],
    },
    "hidden": {
        "sources": [L + "hidden.py", K + "hidden.py", W + "routes_hidden.py", S + "files/hidden.js"],
        "unit": ["test_hidden.py"],
        "e2e": [],
    },
    "results": {
        "sources": [S + "results/**", S + "charts/**", W + "results.py", W + "result_preferences.py", W + "routes_results.py",
                    *_loop("ideas", "eligibility")],
        "unit": ["test_web_results.py", "test_web_result_*.py", "test_web_charts.py", "test_web_ideas*.py", "test_ideas*.py",
                 "test_design_names.py", "test_eligibility.py", "test_web_measurement_summary.py", "test_web_d955.py",
                 "test_dictionary_metrics.py"],
        "e2e": ["start and stop", "the decision and the best", "overview ideas"],
    },
    "overview": {
        "sources": [S + "overview/**", W + "models.py"],
        "unit": ["test_web_overview_layout.py", "test_dictionary_metrics.py"],
        "e2e": [],
    },
    "files": {
        "sources": [S + "files/**", S + "viewer.js", S + "diff.js", S + "highlight.js", W + "gitignore.py", W + "confine.py"],
        "unit": ["test_web_gitignore.py", "test_web_d907.py", "test_web_d908.py", "test_web_confine.py", "test_web_markdown.py"],
        "e2e": ["hidden from AI"],
    },
    "loops": {
        "sources": [S + "loops/**", S + "loops.js", S + "loop/**", S + "loop_page.js", S + "uploads.js", S + "settings/**",
                    W + "routes_loops.py", W + "loop_actions.py", W + "relocate.py", W + "reset.py", W + "stamp.py"],
        "unit": ["test_web.py", "test_web_clone.py", "test_web_d906.py", "test_web_uploads.py", "test_web_share.py",
                 "test_web_relocate.py", "test_web_reset.py", "test_web_loop_actions.py", "test_web_admission.py",
                 "test_web_api_lifecycle.py", "test_loop_names.py", "test_alt_problems.py", "test_web_routes.py"],
        "e2e": ["admin permissions", "admin sharing", "a watcher"],
    },
    "account": {
        "sources": [S + "account/**", S + "account.js", W + "logins.py", W + "routes_accounts.py"],
        "unit": ["test_web_d702.py", "test_web_invites.py", "test_web_external.py", "test_web_groups.py", "test_web_env.py"],
        "e2e": ["user groups"],
    },
    "admin": {
        "sources": [S + "admin/**", S + "admin.js", W + "admin.py", W + "routes_admin.py", W + "maintenance.py", W + "insights.py",
                    W + "history.py", W + "usage.py", W + "usage_log.py", W + "pricing.py", W + "masks.py", W + "cli.py",
                    "scripts/install-service.py"],
        "unit": ["test_web_admin.py", "test_web_maintenance.py", "test_web_insights.py", "test_web_history.py", "test_web_masks.py",
                 "test_web_audit_groups.py", "test_web_last_active.py", "test_web_impersonation.py", "test_web_restart.py",
                 "test_web_d920.py", "test_web_d921.py", "test_web_refusals.py", "test_web_migrate.py", "test_web_partial_usage.py",
                 "test_record_rows.py", "test_service_lifecycle.py", "test_leaks.py"],
        "e2e": ["documents migrated", "raw network setting", "nix packages setting", "sorted lists", "phone"],
    },
    "agents": {
        "sources": [S + "agents/**", W + "agents.py", W + "routes_agents.py",
                    *_loop("agent", "agent_check", "agent_env", "agent_usage", "boxes", "probe", "capabilities")],
        "unit": ["test_agent_*.py", "test_agents_*.py", "test_coding_agent.py", "test_probe.py", "test_workbench.py",
                 "test_prompt_limits.py", "test_web_agent_steps.py", "test_web_d934.py", "test_web_d945.py",
                 "test_web_conversation.py", "test_document_fixes_d967.py", "test_web_turn_words.py"],
        "e2e": ["agent settings", "models from endpoint", "live page"],
    },
    "talk": {
        "sources": [S + "loop/Talk.js", S + "loop/Notes.js", S + "notices.js", W + "asks.py", L + "consult.py",
                    "mentor/feedback/**"],
        "unit": ["test_web_asks.py", "test_human_feedback.py", "test_inbox.py", "test_feedback_*.py"],
        "e2e": ["files and questions", "a watcher"],
    },
    "document": {
        "sources": [L + "document/**", *_loop("migrate", "check", "problem", "task", "inputs", "sources", "graph", "words"),
                    "../website/docs/guide/parameters.md"],
        "unit": ["test_document_*.py", "test_migrate.py", "test_task_document.py", "test_space_*.py", "test_gate_*.py",
                 "test_app_tool_commands.py", "test_evidence_inputs.py", "test_rim_conformance.py", "test_stage_rename.py"],
        "e2e": ["direct edit", "empty loop", "dse setting"],
    },
    "presets": {
        "sources": ["presets/**", *_loop("py2sv", "golden_model", "golden_proto", "pyint/**", "systemc_proto", "prototype",
                                          "toolrun", "toolbox", "toolchain", "tools", "timing")],
        "unit": ["test_flux_presets.py", "test_preset_*.py", "test_prog_commands.py", "test_rtl_*.py", "test_py2sv*.py",
                 "test_champsim_generic.py", "test_trace_staging.py", "test_systemc_prototype.py", "test_golden_prototype.py",
                 "test_prototype_hardware_subset.py", "test_evaluator_tools.py", "test_timing.py", "test_tool_output.py"],
        "e2e": ["preset named", "baseline setting"],
    },
    "knowledge": {
        "sources": ["mentor/knowledge/**", "mentor/records/**", *_loop("skills", "task_knowledge", "recall", "novelty"),
                    "../skills/**"],
        "unit": ["test_knowledge_*.py", "test_library_*.py", "test_mentor_*.py", "test_mined_knowledge.py", "test_skills.py",
                 "test_loop_skills.py", "test_loop_papers.py", "test_flux_skill.py", "test_design_guidance_corpus.py",
                 "test_flux_loop_recall.py", "test_novelty.py", "test_extract_duels.py", "test_campaign_record.py",
                 "test_web_d955.py"],
        "e2e": ["library box", "knowledge skills"],
    },
    "sandbox": {
        "sources": [CLI + "sandbox*", L + "sandbox_paths.py"],
        "unit": ["test_sandbox*.py", "test_leaks.py"],
        "e2e": [],
    },
    "docs": {
        "sources": ["../website/**", "../docs/**", "../*.md", "*.md", "**/README.md", "**/IDEAS.md", "applications/TOOLS.md"],
        "unit": ["test_doc_links.py", "test_docs_crafter.py", "test_document_reference.py", "test_loop_crafter.py"],
        "e2e": [],
    },
    "shell": {
        "sources": [S + "shell/**", S + "scroll.js", S + "logo.svg"],
        "unit": ["test_web_static_imports.py"],
        "e2e": ["every tab", "keyboard journey", "talk clear"],
    },
    "engine": {
        "sources": [*_loop("loop", "types", "baseline", "calibrate", "compute", "cutoff", "direction", "dse", "estimate", "generation", "gradient",
                           "ladder", "ledger", "measure", "measure_cache", "metrics", "model", "objective", "ops", "passes",
                           "patch", "plan", "pool", "provenance", "records", "reevaluate", "rejection", "report", "roles",
                           "task_draft", "task_measure", "task_parts", "task_prototype", "toollock"),
                    "core/frontier/**", "core/stores/**", "core/profile/**", "core/loop/examples/**", "core/loop/README.md",
                    CLI + "main.py", CLI + "__init__.py", CLI + "__main__.py", CLI + "selftest.py", CLI + "tools.py", CLI + "baseline/**"],
        "unit": ["test_loop.py", "test_loop_chain.py", "test_loop_generation_sources.py", "test_loop_live.py", "test_loop_one_step_loop.py",
                 "test_loop_routing.py", "test_loop_search_path.py", "test_loop_subloops.py", "test_ahead.py", "test_artifacts_once.py",
                 "test_baseline_*.py", "test_calibrate_*.py", "test_campaign_*.py", "test_computed_paths.py", "test_decide.py",
                 "test_decision_*.py", "test_dse.py", "test_estimate.py", "test_feasible_decision.py", "test_flux_new.py",
                 "test_frontier_dominance.py", "test_golden_campaign.py", "test_measurement_*.py", "test_model_replies.py",
                 "test_no_direct_metric_indexing.py", "test_objective*.py", "test_one_measurement_two_faces.py", "test_ops.py",
                 "test_parallel_*.py", "test_pareto_uct.py", "test_pass_*.py", "test_plan.py", "test_pool.py", "test_record_*.py",
                 "test_reevaluate.py", "test_report*.py", "test_result_record.py", "test_review_small_fixes.py", "test_roles.py",
                 "test_scope.py", "test_search_policy.py", "test_selftest.py", "test_siblings.py", "test_storage_frontier.py",
                 "test_store*.py", "test_study_sidecars.py", "test_tools_at_once.py", "test_type_checking_imports.py",
                 "test_rim_conformance.py"],
        "e2e": ["start and stop", "passes at once", "a killed run", "error feedback"],
    },
    "llm": {
        "sources": ["core/llm/**", W + "model_list.py", W + "models.py"],
        "unit": ["test_llm_text.py", "test_model_config.py", "test_openai_compat_proposer.py", "test_endpoint_slots.py",
                 "test_web_models.py", "test_web_model_list.py", "test_agent_recovery.py", "test_agents_own_model.py"],
        "e2e": ["models from endpoint"],
    },
    "tui": {
        "sources": ["core/tui/**"],
        "unit": ["test_tui*.py"],
        "e2e": [],
    },
    "apps": {
        "sources": ["applications/**"],
        "unit": ["test_bankmap.py", "test_bingo_storage_model.py", "test_macarray*.py", "test_nlu_*.py", "test_prefetcher_document.py",
                 "test_primes_cheats.py", "test_rim_conformance.py", "test_champsim_generic.py"],
        "e2e": [],
    },
    "repo": {
        "sources": ["../.github/**", "../.gitignore", ".gitignore"],
        "unit": ["test_ci_workflow.py", "test_flake_local_packages.py", "test_areas.py"],
        "e2e": [],
    },
}


@functools.cache
def _regex(glob: str) -> re.Pattern:
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif glob[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif glob[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(glob[i]), i + 1
    return re.compile(out + r"\Z")


def matches(path: str, globs: list[str]) -> bool:
    """`path`, relative to flux/, matches one of `globs` (a glob in flux/ never a path outside it)."""
    return any(_regex(g).match(path) for g in globs if g.startswith("../") == path.startswith("../"))


def matches_any(paths: list[str], glob: str) -> bool:
    return any(matches(p, [glob]) for p in paths)


def unit_files(area: str) -> list[str]:
    """The area's unit test files, `tests/unit/test_*.py`, as names."""
    return sorted({p.name for g in AREAS[area]["unit"] for p in (FLUX / "tests/unit").glob(g)})


# ---- the e2e steps, read from their modules' text (no browser, no import of the harness)
STEPS = FLUX / "tests/e2e/steps"
_STEP = re.compile(r'r\.step\("([^"]+)", \w+(, setup=True)?\)')


def e2e_steps() -> dict[str, tuple[str, bool]]:
    """Every e2e step: its name -> (its module, which is its area; whether it starts from the setup when a
    shard runs it on its own)."""
    return {m.group(1): (p.stem, bool(m.group(2)))
            for p in sorted(STEPS.glob("*.py")) if p.stem != "__init__" for m in _STEP.finditer(p.read_text())}


def e2e_of(area: str, steps: dict[str, tuple[str, bool]] | None = None) -> list[str]:
    """The area's e2e steps: its module's and the ones it names from others."""
    steps = e2e_steps() if steps is None else steps
    return list(dict.fromkeys([s for s, (m, _) in steps.items() if m == area] + AREAS[area]["e2e"]))


def areas_of(path: str) -> list[str] | None:
    """The areas a changed file (relative to flux/) touches; None: everything (shared, or a file in flux/ no area
    names). A unit test file is its own areas', an e2e step module its area's; a file outside flux/ that no
    area names, or an `UNTESTED` one, none."""
    if matches(path, SHARED):
        return None
    if path.startswith("tests/unit/test_") and path.endswith(".py"):
        got = [a for a in AREAS if Path(path).name in unit_files(a)]
        return got or None
    if path.startswith("tests/e2e/steps/") and Path(path).stem in AREAS:
        return [Path(path).stem]
    if path.startswith("tests/integration/") or path == "tests/e2e/web_ui_gallery.py":
        return []                                   # not part of the check: nothing to run for it
    got = [a for a, spec in AREAS.items() if matches(path, spec["sources"])]
    if got or path.startswith("../") or matches(path, UNTESTED):
        return got                                  # outside flux/ and named by no area: another project's
    return None
