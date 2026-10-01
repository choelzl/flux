"""`python -m flux_cli`: the `flux` command, as the sandbox starts it by the running interpreter.
`-m flux_cli.main` would warn, since the package already imports `main` (D716)."""

import sys

from .main import main

sys.exit(main())
