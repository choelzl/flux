"""What the invention loop kept, and which of it deserves a place on the compose menu."""

from __future__ import annotations

import json
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.invented import knob_spaces, library  # noqa: E402

HEADER = "#ifndef X_H\n#define X_H\nclass XPrefetcher {};\n#endif\n"


def _keep(root: Path, name: str, **meta):
    (root / f"{name}.h").write_text(HEADER)
    (root / f"{name}.json").write_text(json.dumps({"name": name, "knobs": {f"{name}_k": 4}, **meta}))


def test_a_design_that_added_nothing_as_a_partner_is_not_offered(tmp_path):
    """An inert design ties its stack exactly; a harmful one drags it down. Neither is a partner."""
    _keep(tmp_path, "good", geomean_alone=1.004, geomean_with_stack=1.0567, reference_geomean=1.0554)
    _keep(tmp_path, "inert", geomean_alone=1.0, geomean_with_stack=1.0554, reference_geomean=1.0554)
    _keep(tmp_path, "worse", geomean_alone=0.9921, geomean_with_stack=1.0494, reference_geomean=1.0554)
    _keep(tmp_path, "harmful", geomean_alone=0.966, geomean_with_stack=0.99, reference_geomean=1.0554)
    assert [i.name for i in library(tmp_path)] == ["good"]


def test_an_older_record_without_a_reference_is_admitted_on_its_solo_number(tmp_path):
    _keep(tmp_path, "old", geomean_alone=1.003, geomean_with_stack=1.062)
    _keep(tmp_path, "oldbad", geomean_alone=0.95, geomean_with_stack=1.02)
    assert [i.name for i in library(tmp_path)] == ["old"]


def test_the_menu_is_ordered_best_partner_first(tmp_path):
    _keep(tmp_path, "a", geomean_alone=1.001, geomean_with_stack=1.060, reference_geomean=1.055)
    _keep(tmp_path, "b", geomean_alone=1.001, geomean_with_stack=1.064, reference_geomean=1.055)
    assert [i.name for i in library(tmp_path)] == ["b", "a"]


def test_knob_spaces_bracket_the_models_defaults():
    from flux_prefetcher.invented import Invention

    inv = Invention(name="x", header=HEADER, knobs={"x_conf": 4, "x_max": 4096}, idea="",
                    geomean_alone=1.0, geomean_with_stack=1.06)
    spaces = knob_spaces([inv])["x"]
    assert spaces["x_conf"] == (4, (1, 2, 4, 8, 16))
    assert spaces["x_max"][0] == 4096 and 1024 in spaces["x_max"][1] and 16384 in spaces["x_max"][1]
