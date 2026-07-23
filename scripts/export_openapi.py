# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Export the FastAPI OpenAPI schema to a JSON file.

Generates the schema offline from the application object (no running server or
database needed) so the committed spec in docs/api/openapi.json can be kept in
sync with the code.

Usage:
    uv run poe export-openapi
    # or directly, with a custom output path:
    uv run python -m scripts.export_openapi --output docs/api/openapi.json
"""

import argparse
import json
from pathlib import Path

from src.app import api

DEFAULT_OUTPUT = Path("docs/api/openapi.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the OpenAPI schema of the FastAPI app to a JSON file.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output file path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    schema = api.openapi()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"OpenAPI schema written to {args.output} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
