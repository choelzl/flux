"""The gate: the architecture is valid Architecture IR. Prints `N failing` (0 or 1)."""

import sys

import yaml
from flux_ir import SchemaValidationError, validate

try:
    validate("architecture", yaml.safe_load(open(sys.argv[1])))
    print("0 failing")
except SchemaValidationError as exc:
    print(f"{exc}\n1 failing")
