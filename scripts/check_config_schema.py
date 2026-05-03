"""Config schema drift detection.

Usage:
    uv run python scripts/check_config_schema.py dump   # write baseline
    uv run python scripts/check_config_schema.py check  # exit non-zero on drift
"""

import difflib
import json
import sys
from pathlib import Path

BASELINE = Path(__file__).parent / "config_schema_baseline.json"


def _get_schema() -> str:
    from src.config.schema import Config

    schema = Config.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True)


def cmd_dump() -> None:
    schema_text = _get_schema()
    BASELINE.write_text(schema_text + "\n")
    print(f"Baseline written to {BASELINE}")


def cmd_check() -> None:
    if not BASELINE.exists():
        print(f"ERROR: baseline not found at {BASELINE}. Run 'dump' first.", file=sys.stderr)
        sys.exit(1)

    current = _get_schema()
    baseline = BASELINE.read_text().rstrip("\n")

    if current == baseline:
        print("Schema OK — no drift detected.")
        return

    diff = list(
        difflib.unified_diff(
            baseline.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile="config_schema_baseline.json",
            tofile="current schema",
        )
    )
    print("Schema drift detected:\n")
    sys.stdout.writelines(diff)
    sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    subcommand = sys.argv[1]
    if subcommand == "dump":
        cmd_dump()
    elif subcommand == "check":
        cmd_check()
    else:
        print(f"Unknown subcommand: {subcommand!r}. Expected 'dump' or 'check'.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
