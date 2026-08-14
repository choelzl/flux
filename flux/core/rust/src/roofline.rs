//! Pure-Rust compute-bound roofline lower bound: `total_macs / lanes` cycles, at best one
//! multiply-accumulate per lane per cycle. Mirrors
//! `validity/src/flux_validity/roofline.py`'s own `_extract_total_macs`/`_extract_lanes`
//! extraction logic exactly (same v0.1 scope: one two-operand `einsum` op with a 3-dim bound;
//! one compute hierarchy node with exactly one spatial dimension) — deliberately
//! *re-implemented*, not shared code, since `validity/roofline.py` independently *checks*
//! another evaluator's own reported `latency_cycles` against this same formula, while this crate
//! *computes* it as a real metric in its own right (docs/decisions.md D75); an evaluator and the
//! thing that independently checks it sharing code would defeat the point (the same reasoning
//! `validity/roofline.py`'s own module docstring already gives for not importing any adapter).
//!
//! Zero `pyo3` dependency here by design — testable via plain `cargo test`, no Python, no
//! libpython link required. `lib.rs`'s `python`-feature-gated module is the only PyO3-aware code
//! in this crate.

use serde_json::Value;

/// Computes the compute-bound lower-bound cycle count for a single two-operand `einsum` op
/// against a single-spatial-dim architecture, both passed as JSON text (the PyO3 boundary passes
/// `&str`, not a parsed structure, to keep the FFI surface trivial). Returns a `String` error
/// (not a custom error type) — this crate's only consumer across the FFI boundary needs a
/// message, not a typed error to match on.
pub fn roofline_latency_cycles(workload_json: &str, arch_json: &str) -> Result<f64, String> {
    let workload: Value =
        serde_json::from_str(workload_json).map_err(|e| format!("invalid workload JSON: {e}"))?;
    let arch: Value =
        serde_json::from_str(arch_json).map_err(|e| format!("invalid arch JSON: {e}"))?;
    let total_macs = extract_total_macs(&workload)?;
    let lanes = extract_lanes(&arch)?;
    if lanes <= 0 {
        return Err(format!("lanes must be positive, found {lanes}"));
    }
    Ok(total_macs as f64 / lanes as f64)
}

pub fn extract_total_macs(workload: &Value) -> Result<i64, String> {
    let empty = Vec::new();
    let ops = workload.get("ops").and_then(Value::as_array).unwrap_or(&empty);
    let einsum_ops: Vec<&Value> = ops
        .iter()
        .filter(|op| op.get("kind").and_then(Value::as_str) == Some("einsum"))
        .collect();
    if einsum_ops.len() != 1 {
        return Err(format!(
            "expected exactly one 'einsum' op, found {} — this crate's v0.1 scope matches \
             evaluators/rtl's own single-op limit, not a new restriction",
            einsum_ops.len()
        ));
    }
    let empty_map = serde_json::Map::new();
    let bounds = einsum_ops[0]
        .get("bounds")
        .and_then(Value::as_object)
        .unwrap_or(&empty_map);
    if bounds.len() != 3 {
        return Err(format!(
            "expected a 3-dim two-operand contraction (e.g. 'B C, C K -> B K'), found {} bound \
             dims — total-MAC-count-by-product only holds for this shape",
            bounds.len()
        ));
    }
    let mut total: i64 = 1;
    for (name, extent) in bounds.iter() {
        let e = extent
            .as_i64()
            .ok_or_else(|| format!("bound extent for {name:?} is not an integer: {extent:?}"))?;
        total *= e;
    }
    Ok(total)
}

pub fn extract_lanes(arch: &Value) -> Result<i64, String> {
    let empty = Vec::new();
    let hierarchy = arch.get("hierarchy").and_then(Value::as_array).unwrap_or(&empty);
    let compute_nodes: Vec<&Value> = hierarchy
        .iter()
        .filter(|n| n.get("class").and_then(Value::as_str) == Some("compute"))
        .collect();
    if compute_nodes.len() != 1 {
        return Err(format!(
            "expected exactly one compute hierarchy node, found {}",
            compute_nodes.len()
        ));
    }
    let empty_map = serde_json::Map::new();
    let dims = compute_nodes[0]
        .get("attrs")
        .and_then(|a| a.get("dims"))
        .and_then(Value::as_object)
        .unwrap_or(&empty_map);
    if dims.len() != 1 {
        return Err(format!(
            "expected exactly one spatial array dimension, found {}",
            dims.len()
        ));
    }
    let (_dim_name, lanes_val) = dims.iter().next().expect("len checked == 1 above");
    lanes_val
        .as_i64()
        .ok_or_else(|| format!("lanes value is not an integer: {lanes_val:?}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // The exact real shapes docs/decisions.md's whole Phase 1 story is built on:
    // ir/workload/examples/mlp-gemm0.yaml (B=4, C=32, K=32 -> 4096 total MACs) and
    // ir/architecture/examples/simple-npu-1d-v1.yaml (pe_array, X=8 lanes) -> the real,
    // already-established 512-cycle bound Timeloop's own real mapper hits exactly
    // (docs/phase1-exit-criterion-report.md) and validity/roofline.py already checks every
    // evaluator's own result against.

    fn mlp_gemm0_workload() -> Value {
        json!({"ops": [{"kind": "einsum", "bounds": {"B": 4, "C": 32, "K": 32}}]})
    }

    fn simple_npu_1d_arch(lanes: i64) -> Value {
        json!({"hierarchy": [
            {"level": "dram", "class": "memory", "attrs": {"size_kb": 1048576}},
            {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
            {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": lanes}}},
        ]})
    }

    #[test]
    fn matches_the_already_established_512_cycle_bound_for_mlp_gemm0_at_8_lanes() {
        let cycles = roofline_latency_cycles(
            &mlp_gemm0_workload().to_string(),
            &simple_npu_1d_arch(8).to_string(),
        )
        .unwrap();
        assert_eq!(cycles, 512.0);
    }

    #[test]
    fn scales_inversely_with_lane_count() {
        let c16 = roofline_latency_cycles(
            &mlp_gemm0_workload().to_string(),
            &simple_npu_1d_arch(16).to_string(),
        )
        .unwrap();
        assert_eq!(c16, 256.0);
        let c4 = roofline_latency_cycles(
            &mlp_gemm0_workload().to_string(),
            &simple_npu_1d_arch(4).to_string(),
        )
        .unwrap();
        assert_eq!(c4, 1024.0);
    }

    #[test]
    fn rejects_a_workload_with_no_einsum_ops() {
        let err = extract_total_macs(&json!({"ops": []})).unwrap_err();
        assert!(err.contains("found 0"), "{err}");
    }

    #[test]
    fn rejects_a_workload_with_multiple_einsum_ops() {
        let workload = json!({"ops": [
            {"kind": "einsum", "bounds": {"B": 1, "C": 1, "K": 1}},
            {"kind": "einsum", "bounds": {"B": 1, "C": 1, "K": 1}},
        ]});
        let err = extract_total_macs(&workload).unwrap_err();
        assert!(err.contains("found 2"), "{err}");
    }

    #[test]
    fn ignores_non_einsum_ops_when_counting() {
        let workload = json!({"ops": [
            {"kind": "data_dependent", "bounds": {}},
            {"kind": "einsum", "bounds": {"B": 4, "C": 32, "K": 32}},
        ]});
        assert_eq!(extract_total_macs(&workload).unwrap(), 4096);
    }

    #[test]
    fn rejects_a_non_3dim_bound_shape() {
        let workload = json!({"ops": [{"kind": "einsum", "bounds": {"B": 1, "C": 1}}]});
        let err = extract_total_macs(&workload).unwrap_err();
        assert!(err.contains("found 2 bound dims"), "{err}");
    }

    #[test]
    fn rejects_an_arch_with_no_compute_node() {
        let err = extract_lanes(&json!({"hierarchy": []})).unwrap_err();
        assert!(err.contains("found 0"), "{err}");
    }

    #[test]
    fn rejects_an_arch_with_more_than_one_compute_node() {
        let arch = json!({"hierarchy": [
            {"class": "compute", "attrs": {"dims": {"X": 8}}},
            {"class": "compute", "attrs": {"dims": {"X": 8}}},
        ]});
        let err = extract_lanes(&arch).unwrap_err();
        assert!(err.contains("found 2"), "{err}");
    }

    #[test]
    fn rejects_an_arch_with_multiple_spatial_dims() {
        let arch = json!({"hierarchy": [{"class": "compute", "attrs": {"dims": {"X": 4, "Y": 4}}}]});
        let err = extract_lanes(&arch).unwrap_err();
        assert!(err.contains("found 2"), "{err}");
    }

    #[test]
    fn rejects_malformed_json() {
        let err = roofline_latency_cycles("not json", "{}").unwrap_err();
        assert!(err.contains("invalid workload JSON"), "{err}");
    }
}
