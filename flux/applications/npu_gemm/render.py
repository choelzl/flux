"""Write one accelerator architecture (Architecture IR) from two knobs: the PE array's width and
the global buffer's size. `python render.py OUT PE_X GBUF_KB`."""

import sys

import yaml

out, pe_x, gbuf_kb = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])     # 0.5: half a KB
gbuf_kb = int(gbuf_kb) if gbuf_kb.is_integer() else gbuf_kb
arch = {
    "schema_version": "0.1.0",
    "id": f"npu/x{pe_x}-g{gbuf_kb}",
    "tech": {"node": "n28", "pdk_class": "open"},
    "hierarchy": [
        {"level": "dram", "class": "memory", "attrs": {"size_kb": 1048576}},
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": gbuf_kb}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": pe_x}}},
    ],
}
with open(out, "w") as f:
    yaml.safe_dump(arch, f, sort_keys=False)
