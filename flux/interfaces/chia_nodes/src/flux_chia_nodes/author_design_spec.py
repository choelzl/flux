"""`flux_author_design_spec` — natural language to a validated, holdout-ready DesignSpec
(docs/decisions.md D235): the implementation-side sibling of D232's objective authoring, and the
step that takes "build me a module that does X" beyond the derivable dot-product family.

The load-bearing choice: the LLM authors the ports and a **Python reference function**, and this
node EXECUTES that reference to compute the golden vectors — expected outputs are computed,
never model-asserted, so the D223 discipline holds: the thing that grades the hardware is a
running program, and a held-out vector set (fresh seeds, same reference) exists by construction.

Trust boundary, stated rather than implied: executing the reference runs LLM-written Python in a
restricted namespace (no imports, no I/O builtins) — the same local-model trust this repo
already extends when it compiles and RUNS LLM-written C++/Verilog through g++ and Verilator.
Restriction here is a guard against accidents, not a sandbox against an adversary; the model is
the same local qwen whose generated binaries already execute.

Validation is the real harness parser (`design_spec_from_dict`) plus executable checks: the
reference must be deterministic (run twice, compared), and every computed output must fit its
declared port width — a reference whose outputs overflow the ports would make correct RTL fail
verification, the exact D193/D228 wrap-vs-unbounded mismatch, caught here at authoring time with
the real error fed back for bounded repair.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_llm import default_local_model, strip_markdown_fence

_DEFAULT_MODEL = default_local_model()
_MAX_REPAIR_ATTEMPTS = 3
_N_SHOWN = 4
_N_HOLDOUT = 8
_MIN_BITS, _MAX_BITS = 2, 64

_PROMPT_TEMPLATE = """Design a combinational hardware module from this request.

Request: {prose}

Output a single JSON object with EXACTLY these fields:
- "module_name": a valid identifier
- "behavior": one paragraph describing the module's function precisely (a later step hands this
  to an RTL writer, so it must fully specify the input->output rule)
- "ports": a list of {{"name": <identifier>, "dir": "in"|"out", "dtype": "int",
  "bits": <int {min_bits}..{max_bits}>}} — signed two's-complement integers; at least one input
  and at least one output; sizes chosen so every possible output VALUE fits its declared width;
  names must not be Verilog reserved words (`output`, `input`, `reg`, `wire`, ... — use names
  like `result` or `sum_out` instead)
- "reference": Python source defining exactly `def reference(inputs):` — takes a dict mapping
  each input port name to a signed int, returns a dict mapping each output port name to the
  exact signed int the hardware must produce. Pure arithmetic and comparisons only: no imports,
  no I/O, no randomness, deterministic. Encode the newlines of this source as \n inside the
  JSON string (single backslash), e.g. "def reference(inputs):\n    return ..."

Output ONLY the JSON object — no markdown fences, no explanation."""

_REPAIR_TEMPLATE = """Your previous design was rejected.

--- your previous JSON ---
{prior}

--- real validation error ---
{error}

Fix it and output ONLY the corrected JSON object — same rules as before."""

# Accidents-guard namespace for the reference: arithmetic-shaped builtins only. Not an
# adversarial sandbox — see the module docstring's trust-boundary paragraph.
_SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "int": int, "bool": bool, "len": len,
    "range": range, "sum": sum, "divmod": divmod, "pow": pow, "round": round,
    "enumerate": enumerate, "zip": zip, "all": all, "any": any,
}


class ReferenceError_(ValueError):
    """The authored reference failed an executable check (crash, nondeterminism, overflow)."""


def _load_reference(source: str):
    # qwen reliably double-escapes the JSON string ("def reference(inputs):\\n    ...") so the
    # decoded source holds literal backslash-n and compiles to "unexpected character after line
    # continuation character" — and three repair rounds did not climb out (the D153 primer
    # class). Normalized only in the unambiguous case: no real newline anywhere.
    if "\n" not in source and "\\n" in source:
        source = source.replace("\\n", "\n").replace("\\t", "    ")
    if "import" in source:
        raise ReferenceError_("the reference must not import anything")
    namespace: dict[str, Any] = {"__builtins__": dict(_SAFE_BUILTINS)}
    exec(compile(source, "<authored-reference>", "exec"), namespace)  # noqa: S102 — see docstring
    fn = namespace.get("reference")
    if not callable(fn):
        raise ReferenceError_("the source must define `def reference(inputs):`")
    return fn


def _signed_range(bits: int) -> tuple[int, int]:
    return -(1 << (bits - 1)), (1 << (bits - 1)) - 1


def _make_vectors(
    ports: list[dict[str, Any]], reference, *, n: int, seed_salt: str, spec_identity: str,
) -> list[dict[str, Any]]:
    """Seeded from the spec's own identity (D223's content-addressed convention): the same
    authored design always grades on the same vectors, and a different salt is a disjoint,
    equally-golden set — the holdout."""
    seed = int.from_bytes(
        hashlib.sha256(f"{spec_identity}:{seed_salt}".encode()).digest()[:8], "big")
    rng = random.Random(seed)
    in_ports = [p for p in ports if p["dir"] == "in"]
    out_ports = [p for p in ports if p["dir"] == "out"]

    vectors = []
    for _ in range(n):
        inputs = {}
        for p in in_ports:
            lo, hi = _signed_range(p["bits"])
            inputs[p["name"]] = rng.randint(lo, hi)
        outputs = reference(dict(inputs))
        again = reference(dict(inputs))
        if outputs != again:
            raise ReferenceError_(f"reference is nondeterministic: {outputs} != {again}")
        if not isinstance(outputs, dict):
            raise ReferenceError_(f"reference returned {type(outputs).__name__}, not a dict")
        expected = {}
        for p in out_ports:
            if p["name"] not in outputs:
                raise ReferenceError_(f"reference returned no value for output {p['name']!r}")
            value = outputs[p["name"]]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ReferenceError_(
                    f"reference returned {value!r} for {p['name']!r} — outputs must be ints")
            lo, hi = _signed_range(p["bits"])
            if not lo <= value <= hi:
                raise ReferenceError_(
                    f"reference output {p['name']}={value} for inputs {inputs} does not fit the "
                    f"declared {p['bits']}-bit signed port range [{lo}, {hi}] — widen the port "
                    "or fix the rule (an overflowing reference makes correct RTL fail)"
                )
            expected[p["name"]] = value
        vectors.append({"inputs": inputs, "expected": expected})
    return vectors


@dataclass
class AuthoredDesignSpec:
    success: bool
    attempts: int
    spec: dict[str, Any] | None  # harness-validated DesignSpec with SHOWN vectors
    holdout_spec: dict[str, Any] | None  # same ports/behavior, fresh-seed vectors
    reference_source: str | None
    error: str | None
    transcript: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "attempts": self.attempts,
            "spec": self.spec,
            "holdout_spec": self.holdout_spec,
            "reference_source": self.reference_source,
            "error": self.error,
            "transcript": list(self.transcript),
        }


@ChiaFunction()
def flux_author_design_spec(
    prose: str,
    *,
    model: str = _DEFAULT_MODEL,
    max_repair_attempts: int = _MAX_REPAIR_ATTEMPTS,
    n_vectors: int = _N_SHOWN,
    llm: Any | None = None,
) -> AuthoredDesignSpec:
    """Author a validated combinational DesignSpec (plus its holdout twin) from prose. The
    validators are real and executable — the harness spec parser, and the authored reference
    run against generated inputs — with every failure fed back for bounded repair. Nothing is
    generated or simulated here; the result is what `flux_generate_rtl_module` consumes."""
    from flux_codegen_rtl_harness import check_not_reserved, design_spec_from_dict

    if llm is None:
        from chia.models.ollama import OllamaLLM

        llm = OllamaLLM(
            model=model,
            system_message="You design digital hardware and write minimal, valid JSON.",
        )

    prompt = _PROMPT_TEMPLATE.format(prose=prose, min_bits=_MIN_BITS, max_bits=_MAX_BITS)
    transcript: list[str] = []
    last_error = ""

    for attempt in range(1, max_repair_attempts + 1):
        transcript.append(f"--- attempt {attempt} prompt ---\n{prompt}")
        raw = strip_markdown_fence(llm.prompt(prompt).result)
        transcript.append(f"--- attempt {attempt} response ---\n{raw}")
        try:
            doc = json.loads(raw)
            if not isinstance(doc, dict):
                raise ValueError(f"expected a JSON object, got {type(doc).__name__}")
            ports = doc.get("ports") or []
            for p in ports:
                if p.get("dtype") != "int":
                    raise ValueError(f"port {p.get('name')!r}: only dtype='int' is supported")
                if not isinstance(p.get("bits"), int) or not _MIN_BITS <= p["bits"] <= _MAX_BITS:
                    raise ValueError(
                        f"port {p.get('name')!r}: bits={p.get('bits')!r} must be an int in "
                        f"[{_MIN_BITS}, {_MAX_BITS}]")
                # the testbench generator's own reserved-word guard, pulled forward to authoring
                # time so the D51-grade error message becomes repair input instead of a crash
                # three nodes downstream (qwen really does name a port `output`)
                check_not_reserved(str(p.get("name") or ""), context="port name")
            check_not_reserved(str(doc.get("module_name") or ""), context="module name")
            reference_source = doc.get("reference") or ""
            reference = _load_reference(reference_source)

            identity = hashlib.sha256(
                json.dumps({"m": doc.get("module_name"), "p": ports, "r": reference_source},
                           sort_keys=True).encode()
            ).hexdigest()
            shown = _make_vectors(ports, reference, n=n_vectors, seed_salt="",
                                  spec_identity=identity)
            holdout = _make_vectors(ports, reference, n=max(_N_HOLDOUT, 2 * n_vectors),
                                    seed_salt="holdout", spec_identity=identity)

            base = {
                "schema_version": "0.1.0",
                "id": f"authored/{doc.get('module_name', 'module')}",
                "module_name": doc.get("module_name"),
                "ports": ports,
                "behavior": doc.get("behavior"),
            }
            spec = dict(base, test_vectors=shown)
            holdout_spec = dict(base, id=base["id"] + "/holdout", test_vectors=holdout)
            design_spec_from_dict(spec)  # the REAL harness validator
            design_spec_from_dict(holdout_spec)
        except Exception as exc:  # noqa: BLE001 — every failure becomes repair input
            last_error = f"{type(exc).__name__}: {exc}"
            transcript.append(f"--- attempt {attempt} validation error ---\n{last_error}")
            prompt = _REPAIR_TEMPLATE.format(prior=raw[:4000], error=last_error[:2000])
            continue
        return AuthoredDesignSpec(
            success=True, attempts=attempt, spec=spec, holdout_spec=holdout_spec,
            reference_source=reference_source, error=None, transcript=transcript)

    return AuthoredDesignSpec(
        success=False, attempts=max_repair_attempts, spec=None, holdout_spec=None,
        reference_source=None, error=last_error, transcript=transcript)
