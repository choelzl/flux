# evaluator/cimloop — compute-in-memory modelling (not built)

CiMLoop models compute-in-memory arrays — analog crossbars and the ADC/DAC periphery around
them. Nothing in this repo evaluates that class of design, so there is no candidate an adapter
could be handed. It is here because the roadmap names it, not because work is in progress.

## Why the directory exists at all

`tests/unit/test_backend_registry_parity.py` checks that every backend with code is registered,
and treats `cimloop`, `hammer` and `sparseloop` as deliberate placeholders rather than missing
registrations. That check needs somewhere to point, and a reader deserves to find the reason here
rather than in a test's docstring.

## If you build it

Follow `evaluator/README.md`: CHIA ships no cimloop integration, so this would wrap the tool
directly, the way `evaluator/timeloop/` and `evaluator/booksim/` do — and implement the same
`flux_evaluator_abi` `Evaluator` protocol as every other rung, so it is interchangeable with them.
