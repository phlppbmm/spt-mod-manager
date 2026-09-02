"""Write the OpenAPI schema of the server to disk for frontend type generation."""

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sptmod.server.app import create_app

USAGE = "usage: python -m sptmod.server.dump_openapi [outfile]"

DEFAULT_TARGET = Path("openapi.json")


def dump(target: Path) -> None:
    """Write the OpenAPI schema of the application to ``target``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    schema = create_app().openapi()
    target.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str]) -> int:
    """Run the dump for the command line arguments ``argv``."""
    if len(argv) > 1:
        print(USAGE, file=sys.stderr)
        return 2
    dump(Path(argv[0]) if argv else DEFAULT_TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
