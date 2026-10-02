# primes

A Python problem for Flux, written by `flux new primes --kind python`. The model writes
`count_primes(n)`; `check.py` refuses a wrong one (the gate); `bench.py` times the survivors
(the stage); the loop keeps the fastest and asks for faster.

| file | what it is |
|---|---|
| `problem.yaml` | the ask: statement, contract, gate, stage, objective, budget |
| `check.py` | the gate: known cases against a reference, prints `N failing of M` |
| `bench.py` | the stage: times the candidate, prints `time_ms=` |

    flux task check applications/primes
    flux task run applications/primes --passes 1      # one pass; without --passes it runs until stopped

A model is needed: a local Ollama, or `FLUX_REMOTE_BASE_URL` / `FLUX_REMOTE_MODEL` for a server
(README.md, "A run with a model"). To make it yours, change the statement and the contract,
put your own cases in `check.py` and your own workload in `bench.py`.
