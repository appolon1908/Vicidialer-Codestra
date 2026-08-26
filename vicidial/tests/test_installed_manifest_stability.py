from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "vicidial/scripts/generate_installed_manifest.py"
SHA = "a" * 40
GENERATED_AT = "2026-08-26T00:00:00Z"


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_installed_manifest", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_bytecode_does_not_invalidate_installed_manifest(tmp_path: Path) -> None:
    generator = load_generator()

    for directory in generator.INCLUDED:
        target = tmp_path / directory
        target.mkdir(parents=True)
        (target / "controlled.txt").write_text(directory + "\n", encoding="utf-8")

    for name in generator.INCLUDED_FILES:
        (tmp_path / name).write_text(name + "\n", encoding="utf-8")

    expected = generator.build(tmp_path, SHA, GENERATED_AT)

    # Importing the generator during verification may create this cache after
    # the manifest has already been written. Runtime bytecode is intentionally
    # outside the immutable installed-release identity.
    scripts_cache = tmp_path / "scripts/__pycache__"
    scripts_cache.mkdir()
    (scripts_cache / "generate_installed_manifest.cpython-311.pyc").write_bytes(
        b"runtime-bytecode"
    )

    venv_cache = tmp_path / ".venv/lib/python3.11/site-packages/example/__pycache__"
    venv_cache.mkdir(parents=True)
    (venv_cache / "module.cpython-311.pyc").write_bytes(b"runtime-bytecode")

    assert generator.build(tmp_path, SHA, GENERATED_AT) == expected

    # Controlled release bytes remain manifest-bound.
    (tmp_path / "scripts/controlled.txt").write_text("tampered\n", encoding="utf-8")
    assert generator.build(tmp_path, SHA, GENERATED_AT) != expected
