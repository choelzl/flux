"""Unit tests for flux_codegen_rtl_harness.cache (docs/decisions.md D89): pure content-hashing
and SQLite round-trip logic — no real Yosys call needed. See tests/integration/test_rtl_synth_live.py
for the real-Yosys, real-cache-hit version.
"""

from __future__ import annotations

from flux_codegen_rtl_harness import ToolResultCache, content_key


def test_content_key_is_deterministic():
    a = content_key("module X;", "X", {"leaf": "module Y; endmodule"})
    b = content_key("module X;", "X", {"leaf": "module Y; endmodule"})
    assert a == b


def test_content_key_differs_for_different_source():
    a = content_key("module X;", "X", {})
    b = content_key("module X_changed;", "X", {})
    assert a != b


def test_content_key_differs_for_different_extra_sources():
    a = content_key("module X;", "X", {"leaf": "v1"})
    b = content_key("module X;", "X", {"leaf": "v2"})
    assert a != b


def test_content_key_is_a_real_sha256_hex_digest():
    key = content_key("anything")
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


def test_cache_round_trips_a_real_put_get(tmp_path):
    with ToolResultCache(tmp_path / "cache.db") as cache:
        key = content_key("module X;", "X", {})
        assert cache.get(key) is None  # a real, honest miss before anything is stored

        cache.put(key, {"total_cells": 42, "cells_by_type": {"$_AND_": 10}})
        assert cache.get(key) == {"total_cells": 42, "cells_by_type": {"$_AND_": 10}}


def test_cache_persists_across_reopening_the_same_db_path(tmp_path):
    db_path = tmp_path / "cache.db"
    key = content_key("module X;", "X", {})

    with ToolResultCache(db_path) as cache:
        cache.put(key, {"total_cells": 7, "cells_by_type": {}})

    with ToolResultCache(db_path) as cache:
        assert cache.get(key) == {"total_cells": 7, "cells_by_type": {}}


def test_put_overwrites_an_existing_key(tmp_path):
    with ToolResultCache(tmp_path / "cache.db") as cache:
        key = content_key("module X;", "X", {})
        cache.put(key, {"total_cells": 1, "cells_by_type": {}})
        cache.put(key, {"total_cells": 2, "cells_by_type": {}})
        assert cache.get(key) == {"total_cells": 2, "cells_by_type": {}}
