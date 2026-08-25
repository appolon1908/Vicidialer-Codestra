#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codestra_vicidial.release import build_release_document


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate validated Codestra VICIdial R3 BUILD_INFO.json"
        ),
    )
    parser.add_argument("--git-sha", required=True)
    parser.add_argument(
        "--environment",
        choices=(
            "staging-read-only",
            "production-candidate",
            "production",
        ),
        default="production-candidate",
    )
    parser.add_argument("--built-at")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    built_at = args.built_at or (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    document = build_release_document(
        git_sha=args.git_sha,
        built_at=built_at,
        environment=args.environment,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o644)
    temporary.replace(output)

    print(f"BUILD_INFO={output}")
    print(f"GIT_SHA={document['git_sha']}")
    print(f"BUILD_ID={document['build_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
