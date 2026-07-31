# containers/ — evaluator backend Dockerfiles

One Dockerfile per evaluator/generation backend that needs one (Timeloop's islpy/Barvinok build,
Verilator, Hammer's commercial CAD front-ends, ...). No source builds on the critical path for a
new user's first result.

See [docs/03.md G11](../docs/03.md).
