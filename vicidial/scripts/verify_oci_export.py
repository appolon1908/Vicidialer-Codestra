#!/usr/bin/env python3.11
"""Prove an OCI export is the tested image and carries required attestations."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

import slsa_attestation_contracts as _slsa  # noqa: E402
import spdx_attestation_contracts as _spdx  # noqa: E402

_CORE = runpy.run_path(str(_SCRIPT_DIRECTORY / "oci_export_verifier_core.py"))
_OVERRIDES = {
    "SBOM_PREDICATE": _spdx.SBOM_PREDICATE,
    "SLSA_PROVENANCE_PREDICATE": _slsa.SLSA_PROVENANCE_PREDICATE,
    "validate_spdx_predicate": _spdx.validate_spdx_predicate,
    "validate_slsa_provenance": _slsa.validate_slsa_provenance,
}
_CORE.update(_OVERRIDES)
_CORE["main"].__globals__.update(_OVERRIDES)

for _name, _value in _CORE.items():
    if not _name.startswith("__"):
        globals()[_name] = _value
for _module in (_spdx, _slsa):
    for _name in dir(_module):
        if not _name.startswith("_"):
            globals()[_name] = getattr(_module, _name)

if __name__ == "__main__":
    raise SystemExit(_CORE["main"]())
