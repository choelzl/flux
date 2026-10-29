# Example applications

The problem documents of the loops bundled with Flux, as they ship, each with its README. They show
the document format on real problems: software and RTL (primes, adder16, mul8, pto), memory and
compute architecture (bankmap, macarray), a composed loop with sub-loops (nlu) and a cache
prefetcher (prefetcher).

They are for reading: the scripts, libraries, traces and records they call are part of the Flux
source, which is not public. The documentation explains every field:
<https://choelzl.github.io/flux/>.


- [adder16](adder16/): `problem.yaml`, `README.md`
- [bankmap](bankmap/): `problem.yaml`, `README.md`
- [macarray](macarray/): `invent.problem.yaml`, `problem.yaml`, `workload.yaml`, `README.md`
- [mul8](mul8/): `problem.yaml`, `README.md`
- [nlu](nlu/): `problem.yaml`, `README.md`
- [prefetcher](prefetcher/): `invent.problem.yaml`, `problem.yaml`, `README.md`
- [primes](primes/): `problem.yaml`, `README.md`
- [pto](pto/): `problem.yaml`, `README.md`
