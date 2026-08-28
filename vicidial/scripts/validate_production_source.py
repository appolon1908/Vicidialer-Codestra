#!/usr/bin/env python3
"""Fail closed when production-owned Python contains placeholder implementations."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = (
    ROOT / "campaign_provisioning",
    ROOT / "vicidial" / "src",
    ROOT / "scripts" / "vicidial_sync.py",
    ROOT / "scripts" / "canonical_event_client.py",
)
DEPLOYMENT_ROOTS = (
    ROOT / ".github" / "workflows",
    ROOT / "deploy",
    ROOT / "vicidial" / "docker",
    ROOT / "vicidial" / "ops",
)
LEGACY_RUNTIME_NAMES = (
    "vicidial_sync.py.candidate",
    "canonical_event_client.py.reference",
)


def iter_python_files():
    for root in PRODUCTION_ROOTS:
        if root.is_file():
            yield root
            continue
        if not root.exists():
            raise RuntimeError(f"required production source missing: {root}")
        for path in sorted(root.rglob("*.py")):
            if "tests" not in path.parts and "__pycache__" not in path.parts:
                yield path


def is_not_implemented_raise(node: ast.stmt) -> bool:
    if not isinstance(node, ast.Raise) or node.exc is None:
        return False
    exc = node.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"


def validate_python_file(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant
        ) and isinstance(body[0].value.value, str):
            body = body[1:]
        if len(body) == 1 and isinstance(body[0], ast.Pass):
            findings.append(
                f"{path}:{node.lineno}: function {node.name} is pass-only"
            )
        if len(body) == 1 and is_not_implemented_raise(body[0]):
            findings.append(
                f"{path}:{node.lineno}: function {node.name} raises NotImplementedError"
            )
        if len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant
        ) and body[0].value.value is Ellipsis:
            findings.append(
                f"{path}:{node.lineno}: function {node.name} is ellipsis-only"
            )
    return findings


def validate_deployment_paths() -> list[str]:
    findings: list[str] = []
    for root in DEPLOYMENT_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(errors="ignore")
            for legacy in LEGACY_RUNTIME_NAMES:
                if legacy in text:
                    findings.append(
                        f"{path}: deployment authority references legacy runtime {legacy}"
                    )
    return findings


def main() -> int:
    findings: list[str] = []
    for path in iter_python_files():
        findings.extend(validate_python_file(path))
    findings.extend(validate_deployment_paths())
    if findings:
        print("PRODUCTION_SOURCE_COMPLETENESS=FAIL")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("PRODUCTION_SOURCE_COMPLETENESS=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
