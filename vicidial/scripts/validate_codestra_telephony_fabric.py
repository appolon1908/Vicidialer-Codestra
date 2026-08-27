#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

def validate() -> None:
    path = ROOT / 'docs' / 'integrations' / 'codestra-fabric' / 'manifest.v1.json'
    with path.open('r', encoding='utf-8') as handle:
        manifest = json.load(handle)
    assert manifest['integration_boundary'] == 'MIDDLEWARE_RESTRICTED_ADAPTER_ONLY'
    assert manifest['public_api'] is False
    assert manifest['n8n_direct_access'] is False
    assert manifest['database_direct_access'] is False
    assert manifest['ami_direct_access'] is False
    assert manifest['unknown_command_reconcile_before_retry'] is True
    assert manifest['service_identity'] == 'vicidial-adapter'
    assert not any(manifest['capabilities'].values())

if __name__ == '__main__':
    validate()
    print('VICIDIAL_CODESTRA_TELEPHONY_FABRIC=PASS')
