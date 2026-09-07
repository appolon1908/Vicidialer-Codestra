"""Reproduce publication checks without deploying or contacting telephony."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone

SOURCE = '9ac8ef4840f78ba4ad9b816e4e409298505103ce'
BUILDER = 'def0f1822fe54ab2a0800e8d6e083533df88d064'
ARCHIVE = f'codestra-vicidial-adapter-{SOURCE}.tar.gz'
DIGEST = '5e3d4083dd94297c1085a28953547d5532e07c9685560184047208ca2cf0a252'
IDENTITY = 'https://github.com/appolon1908-hue/Vicidialer-Codestra/.github/workflows/publish-server-b-source-release.yml@refs/heads/main'
parser = argparse.ArgumentParser()
for name in ('artifact', 'source', 'builder', 'cosign', 'output'):
    parser.add_argument('--' + name, type=Path, required=True)
a = parser.parse_args()
for name in vars(a):
    setattr(a, name, getattr(a, name).resolve())
a.output.mkdir(parents=True, exist_ok=True)
checks = []
record = {
    'started_at_utc': datetime.now(timezone.utc).isoformat(),
    'invocation': [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
    'verifier_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'source_sha': SOURCE, 'builder_sha': BUILDER,
    'publication_run_attempt': '34000565825/1', 'artifact_id': 9979357196,
    'environment': {'python': sys.version, 'platform': platform.platform(),
                    'uid': os.geteuid(), 'cwd': str(Path.cwd())},
    'checks': checks,
}

def save():
    (a.output / 'audit.json').write_text(json.dumps(record, indent=2) + '\n')

def run(name, argv, *, umask=-1):
    started = datetime.now(timezone.utc).isoformat()
    result = subprocess.run([str(v) for v in argv], cwd=a.artifact,
                            capture_output=True, text=True, umask=umask)
    log = a.output / (name + '.txt')
    log.write_text('STDOUT\n' + result.stdout + '\nSTDERR\n' + result.stderr)
    checks.append({'name': name, 'argv': [str(v) for v in argv],
                   'cwd': str(a.artifact), 'umask': oct(umask) if umask >= 0 else 'inherited',
                   'started_at_utc': started, 'finished_at_utc': datetime.now(timezone.utc).isoformat(),
                   'exit_code': result.returncode, 'result': 'PASS' if result.returncode == 0 else 'FAIL',
                   'output': log.name, 'output_sha256': hashlib.sha256(log.read_bytes()).hexdigest()})
    save()
    if result.returncode:
        raise SystemExit(f'{name} failed; see {log}')

run('tool-git', ['git', '--version'])
run('tool-cosign-checksum', [sys.executable, '-c', "import hashlib,sys; d=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest(); print(d); assert d=='f7622ed3cf22e55e1ae6377c080979ff77a22da9981c11df222a2e444991e7cf'", a.cosign])
run('tool-cosign-version', [a.cosign, 'version'])
for name, path, expected in [('source', a.source, SOURCE), ('builder', a.builder, BUILDER)]:
    run(name + '-identity', [sys.executable, '-c', "import subprocess,sys; value=subprocess.check_output(['git','-C',sys.argv[1],'rev-parse','HEAD'],text=True).strip(); print(value); assert value==sys.argv[2]; subprocess.run(['git','-C',sys.argv[1],'diff','--exit-code','HEAD'],check=True)", path, expected])
run('zip-archive-manifest', [sys.executable, '-c', r'''import hashlib,json,tarfile
from pathlib import Path
zip_digest=hashlib.sha256(Path('artifact.zip').read_bytes()).hexdigest()
assert zip_digest=='15fe0e95c664ddea96c86b181136b12a30e01d7d96ddc2d7d25cddb57c783f49'
name='codestra-vicidial-adapter-9ac8ef4840f78ba4ad9b816e4e409298505103ce.tar.gz'
digest=hashlib.sha256(Path(name).read_bytes()).hexdigest()
assert digest=='5e3d4083dd94297c1085a28953547d5532e07c9685560184047208ca2cf0a252'
assert Path(name+'.sha256').read_text()==digest+'  '+name+'\n'
with tarfile.open(name,'r:gz') as tar: content=tar.extractfile('./SOURCE_RELEASE_MANIFEST.json').read()
manifest=json.loads(content); metadata=json.loads(Path('source-release-metadata.json').read_text())
assert all(metadata[k]==v for k,v in manifest.items())
assert metadata['archive_sha256']==digest
assert metadata['manifest_sha256']==hashlib.sha256(content).hexdigest()=='84275a426d21edecb121334a58af4f69219a4320d071b2352a996a8f8f739bdb'
print(json.dumps({'zip_sha256':zip_digest,'archive_sha256':digest,'manifest':manifest},sort_keys=True))'''])
run('archive-members', [sys.executable, a.builder / 'vicidial/scripts/verify_source_release.py', '--archive', a.artifact / ARCHIVE, '--source-sha', SOURCE, '--builder-sha', BUILDER])
for name, bundle, payload in [('archive-signature','source-release.sigstore.json',ARCHIVE), ('provenance-signature','source-release-provenance.sigstore.json','source-release-provenance.json')]:
    run(name, [a.cosign, 'verify-blob', '--bundle', bundle, '--certificate-identity', IDENTITY, '--certificate-oidc-issuer', 'https://token.actions.githubusercontent.com', payload])
run('provenance-bindings', [sys.executable, '-c', r'''import json,hashlib
from pathlib import Path
s=json.loads(Path('source-release-provenance.json').read_text())
name='codestra-vicidial-adapter-9ac8ef4840f78ba4ad9b816e4e409298505103ce.tar.gz'
assert s['_type']=='https://in-toto.io/Statement/v1'
assert s['predicateType']=='https://slsa.dev/provenance/v1'
assert s['subject']==[{'name':name,'digest':{'sha256':hashlib.sha256(Path(name).read_bytes()).hexdigest()}}]
d=s['predicate']['buildDefinition']; r=s['predicate']['runDetails']
assert d['buildType']=='https://github.com/appolon1908-hue/Vicidialer-Codestra/server-b-source-release/v1'
assert d['externalParameters']=={'source_sha':'9ac8ef4840f78ba4ad9b816e4e409298505103ce','builder_sha':'def0f1822fe54ab2a0800e8d6e083533df88d064'}
assert d['resolvedDependencies']==[{'uri':'git+https://github.com/appolon1908-hue/Vicidialer-Codestra','digest':{'gitCommit':'9ac8ef4840f78ba4ad9b816e4e409298505103ce'}}]
assert r['builder']['id']=='appolon1908-hue/Vicidialer-Codestra/.github/workflows/publish-server-b-source-release.yml@refs/heads/main'
assert r['metadata']['invocationId']=='34000565825/1'
print(json.dumps(s,sort_keys=True))'''])
for mask in (0o022,0o027,0o077):
    directory=a.artifact/'audited-rebuild'/format(mask,'04o'); directory.mkdir(parents=True,exist_ok=True)
    run('rebuild-'+format(mask,'04o'), [sys.executable,a.builder/'vicidial/scripts/build_source_release.py','--source-root',a.source/'vicidial','--source-sha',SOURCE,'--builder-sha',BUILDER,'--output',directory/ARCHIVE,'--metadata',directory/'metadata.json'],umask=mask)
    run('rebuild-digest-'+format(mask,'04o'), [sys.executable,'-c',"import hashlib,sys; d=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest(); print(d); assert d==sys.argv[2]",directory/ARCHIVE,DIGEST])
record['completed_at_utc']=datetime.now(timezone.utc).isoformat()
save()
print('AUDITED_VERIFICATION=PASS')
