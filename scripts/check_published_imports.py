"""Post-publish import check for the removed-name map (MERISTEM-HARNESS-CYCLE-R089.3).

Run from a fresh virtual environment that holds only the published wheel:
every ``new`` name of ``tests/removed_paths.toml`` must import, and a
``HostPorts`` with every field set must build without module-level provider state.
"""

from __future__ import annotations

import dataclasses
import importlib
import sys
import tomllib
from pathlib import Path


def _resolve(entry: dict, fields: set[str]) -> bool:
    dotted = entry["new"]
    if entry["kind"] == "module":
        importlib.import_module(dotted)
        return True
    module, _, name = dotted.rpartition(".")
    if module.endswith("HostPorts"):
        return name in fields
    return hasattr(importlib.import_module(module), name)


def main(map_path: str) -> int:
    from meristem import host_ports

    fields = {f.name for f in dataclasses.fields(host_ports.HostPorts)}
    data = tomllib.loads(Path(map_path).read_text(encoding="utf-8"))
    missing = [e["new"] for e in data["removed"] if not _resolve(e, fields)]
    host_ports.HostPorts(**{name: object() for name in sorted(fields)})
    for name in missing:
        print(f"missing: {name}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "tests/removed_paths.toml"))
