//! Native evaluation core for Flux (docs/architecture.md's L "hot loop"/PyO3-boundary target,
//! never previously built — see docs/decisions.md D33/D75).
//!
//! **The first real, genuinely native, in-repo cost model this repo has** (docs/decisions.md
//! D75): a compute-bound roofline lower bound (`total_macs / lanes` cycles), unlike every other
//! evaluator here (`zigzag`/`timeloop`/`rtl`/`systemc`/`booksim`/`noxim`/`cacti`/`gem5`/
//! `thermal`/`dramsim3`), which all shell out to or import an external tool — D33's own real
//! profiling found flux's own orchestration code is already under 0.1% of wall time for every one
//! of those, so a native rewrite of *them* would buy nothing; a native core only pays off for a
//! cost model that never leaves Rust in the first place, which is what `roofline` is.
//!
//! `roofline` is pure Rust — zero `pyo3` dependency, unit-tested with plain `cargo test`, no
//! Python or libpython link needed. The `python` Cargo feature (off by default) adds the thin
//! PyO3 extension-module edge below, matching docs/architecture.md's own stated principle:
//! "Language boundary: PyO3/nanobind; batch across the boundary, never per-candidate" —
//! `roofline_latency_cycles_batch` is the batched entry point; `roofline_latency_cycles` (single)
//! exists for the one-candidate ABI call shape every other evaluator here also supports.

mod flat_mapping;
mod roofline;

#[cfg(feature = "python")]
mod py {
    use super::flat_mapping;
    use super::roofline;
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;
    use std::collections::BTreeMap;

    #[pyfunction]
    fn roofline_latency_cycles(workload_json: &str, arch_json: &str) -> PyResult<f64> {
        roofline::roofline_latency_cycles(workload_json, arch_json).map_err(PyValueError::new_err)
    }

    /// Batched across the FFI boundary in one call — one workload against many full architecture
    /// documents (e.g. an architecture-width sweep expressed as N separate Architecture IR
    /// candidates), the real shape docs/architecture.md's own "batch across the boundary, never
    /// per-candidate" principle asks for. **Honestly dominated by per-candidate JSON parsing**
    /// (docs/decisions.md D75 measured this directly: ~7.2x10^5 evals/s here, actually *slower*
    /// than the equivalent pure-Python loop over already-parsed dicts at ~1.6x10^6 evals/s) — IR
    /// translation is real, non-trivial work, not a hot-loop primitive; see
    /// `roofline_latency_cycles_for_lane_sweep` below for the actually-representative hot-loop
    /// shape (numeric in, numeric out, no per-candidate parsing).
    #[pyfunction]
    fn roofline_latency_cycles_batch(workload_json: &str, arch_jsons: Vec<String>) -> PyResult<Vec<f64>> {
        arch_jsons
            .iter()
            .map(|arch_json| roofline::roofline_latency_cycles(workload_json, arch_json))
            .collect::<Result<Vec<f64>, String>>()
            .map_err(PyValueError::new_err)
    }

    /// The genuine "hot loop" shape docs/architecture.md's Performance-engineering table
    /// describes: IR translation (extracting `total_macs` from a Workload IR document) happens
    /// **once**, outside this call; the hot loop itself takes and returns plain numeric
    /// SoA-style buffers (`Vec<i64>` in, `Vec<f64>` out) — no per-candidate JSON, no
    /// per-candidate allocation beyond the one output `Vec`. This is what a real
    /// architecture-width DSE sweep's inner loop looks like once IR parsing is hoisted out of
    /// it, and the honest, representative benchmark for docs/architecture.md's own
    /// ">=10^5 dense-layer mapping evaluations/second/core" target (docs/decisions.md D75).
    #[pyfunction]
    fn roofline_latency_cycles_for_lane_sweep(total_macs: i64, lanes: Vec<i64>) -> PyResult<Vec<f64>> {
        lanes
            .iter()
            .map(|&l| {
                if l <= 0 {
                    Err(format!("lanes must be positive, found {l}"))
                } else {
                    Ok(total_macs as f64 / l as f64)
                }
            })
            .collect::<Result<Vec<f64>, String>>()
            .map_err(PyValueError::new_err)
    }

    /// The real, branchy per-candidate computation D75's own Implications named as the next
    /// native-core target (docs/decisions.md D76) — a faithful port of `search/exhaustive`'s own
    /// `_largest_divisor_at_most`, not a new formula.
    #[pyfunction]
    fn largest_divisor_at_most(bound: i64, limit: i64) -> i64 {
        flat_mapping::largest_divisor_at_most(bound, limit)
    }

    /// Every (spatial-split-dim x temporal-loop-order) flat-mapping candidate for the given loop
    /// dims/bounds/array size, as `(spatial_dim, spatial_size, temporal_order)` tuples — mirrors
    /// `search/exhaustive/candidates.py`'s own `generate_flat_mapping_candidates`, minus the
    /// Mapping-IR-document construction (that stays real IR-construction work in Python).
    #[pyfunction]
    fn generate_flat_mapping_candidates(
        loop_dims: Vec<String>, bounds: BTreeMap<String, i64>, array_size: i64,
    ) -> Vec<(String, i64, Vec<String>)> {
        flat_mapping::generate_flat_mapping_candidates(&loop_dims, &bounds, array_size)
            .into_iter()
            .map(|c| (c.spatial_dim, c.spatial_size, c.temporal_order))
            .collect()
    }

    #[pymodule]
    fn flux_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(roofline_latency_cycles, m)?)?;
        m.add_function(wrap_pyfunction!(roofline_latency_cycles_batch, m)?)?;
        m.add_function(wrap_pyfunction!(roofline_latency_cycles_for_lane_sweep, m)?)?;
        m.add_function(wrap_pyfunction!(largest_divisor_at_most, m)?)?;
        m.add_function(wrap_pyfunction!(generate_flat_mapping_candidates, m)?)?;
        Ok(())
    }
}
