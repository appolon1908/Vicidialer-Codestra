"""Execute the publication workflow's post-signature semantic verification."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SHA = '9ac8ef4840f78ba4ad9b816e4e409298505103ce'
WORKFLOW = 'appolon1908-hue/Vicidialer-Codestra/.github/workflows/publish-server-b-source-release.yml@refs/heads/main'


def statement(archive):
    return {
        '_type': 'https://in-toto.io/Statement/v1',
        'predicateType': 'https://slsa.dev/provenance/v1',
        'subject': [{'name': archive.name, 'digest': {'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}}],
        'predicate': {
            'buildDefinition': {
                'buildType': 'https://github.com/appolon1908-hue/Vicidialer-Codestra/server-b-source-release/v1',
                'externalParameters': {'source_sha': SHA, 'builder_sha': 'b' * 40},
                'resolvedDependencies': [{'uri': 'git+https://github.com/appolon1908-hue/Vicidialer-Codestra', 'digest': {'gitCommit': SHA}}],
            },
            'runDetails': {'builder': {'id': WORKFLOW}, 'metadata': {'invocationId': '12345/2'}},
        },
    }


def invoke(tmp_path, mutation=None):
    dist = tmp_path / 'dist'
    dist.mkdir()
    archive = dist / ('codestra-vicidial-adapter-' + SHA + '.tar.gz')
    archive.write_bytes(b'synthetic artifact for semantic-binding tests')
    value = statement(archive)
    if mutation:
        mutation(value)
    (dist / 'source-release-provenance.json').write_text(json.dumps(value))
    workflow = yaml.safe_load((ROOT / '.github/workflows/publish-server-b-source-release.yml').read_text())
    step = next(s for s in workflow['jobs']['publish']['steps'] if s.get('name') == 'Sign and verify archive')
    code = step['run'].split('python - "$archive" <<\'PY\'\n', 1)[1].rsplit('\nPY', 1)[0]
    return subprocess.run([sys.executable, '-c', code, str(archive)], cwd=tmp_path,
                          env={**os.environ, 'SOURCE_SHA': SHA, 'GITHUB_SHA': 'b' * 40, 'GITHUB_WORKFLOW_REF': WORKFLOW,
                               'GITHUB_RUN_ID': '12345', 'GITHUB_RUN_ATTEMPT': '2'}, capture_output=True)


def test_correct_signed_content_bindings(tmp_path):
    assert invoke(tmp_path).returncode == 0


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(predicate={}),
    lambda v: v['predicate']['buildDefinition']['externalParameters'].update(builder_sha='c' * 40),
    lambda v: v.update(predicate='malformed'),
    lambda v: v.update(_type='wrong'),
    lambda v: v.update(predicateType='wrong'),
    lambda v: v['subject'][0]['digest'].update(sha256='0' * 64),
    lambda v: v.update(subject=[]),
    lambda v: v['predicate']['buildDefinition'].update(buildType='wrong'),
    lambda v: v['predicate']['buildDefinition']['externalParameters'].update(source_sha='0' * 40),
    lambda v: v['predicate']['buildDefinition']['resolvedDependencies'][0].update(uri='wrong'),
    lambda v: v['predicate']['runDetails']['builder'].update(id='unprotected-builder'),
    lambda v: v['predicate']['runDetails']['metadata'].update(invocationId='12346/2'),
    lambda v: v['predicate']['runDetails']['metadata'].update(invocationId='12345/1'),
])
def test_wrong_signed_content_bindings_rejected(tmp_path, mutation):
    assert invoke(tmp_path, mutation).returncode != 0
