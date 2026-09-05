import hashlib, json, os, subprocess, sys, tarfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = Path(os.environ.get("SOURCE_RELEASE_TEST_ROOT", ROOT / "vicidial")).resolve()
SCRIPT = ROOT / "vicidial/scripts/build_source_release.py"
VERIFY = ROOT / "vicidial/scripts/verify_source_release.py"
SHA = "75f48ffe9f4a48f76bc5ed0ecb3dc5770933d98a"

def build(tmp_path, name):
    output = tmp_path / name; metadata = tmp_path / f"{name}.json"
    subprocess.run([sys.executable, str(SCRIPT), "--source-root", str(SOURCE_ROOT), "--source-sha", SHA, "--builder-sha", "b" * 40, "--output", str(output), "--metadata", str(metadata)], check=True)
    return output, json.loads(metadata.read_text())

def test_archive_matches_existing_installer_contract(tmp_path):
    archive, metadata = build(tmp_path, "release.tar.gz")
    with tarfile.open(archive, "r:gz") as bundle:
        names = set(bundle.getnames()); assert {"./pyproject.toml", "./src/codestra_vicidial/app.py", "./scripts/validate_runtime.py"} <= names
        assert bundle.extractfile("./SOURCE_SHA").read() == (SHA + "\n").encode()
        manifest_bytes = bundle.extractfile("./SOURCE_RELEASE_MANIFEST.json").read(); manifest = json.loads(manifest_bytes)
        assert manifest["source_sha"] == SHA and manifest["installer_contract"] == "systemd-python-source-v1"
        sums = bundle.extractfile("./SHA256SUMS").read().decode(); assert "  pyproject.toml\n" in sums and "  scripts/validate_runtime.py\n" in sums
        assert all(".git" not in name and "evidence/" not in name and "reports/" not in name for name in names)
    assert metadata["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert metadata["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()

def test_archive_is_deterministic(tmp_path):
    first, _ = build(tmp_path, "first.tar.gz"); second, _ = build(tmp_path, "second.tar.gz"); assert first.read_bytes() == second.read_bytes()

def test_archive_is_accepted_by_existing_pip_install_step(tmp_path):
    archive, _ = build(tmp_path, "release.tar.gz"); extracted = tmp_path / "release"; extracted.mkdir()
    with tarfile.open(archive, "r:gz") as bundle: bundle.extractall(extracted, filter="data")
    target = tmp_path / "installed"
    result = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(extracted)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    check = subprocess.run(
        [sys.executable, "-c", "import importlib.metadata as m; import codestra_vicidial.app as a; assert m.version('codestra-vicidial-adapter') == '0.2.0rc1'; assert a.__file__.startswith(r'%s')" % target],
        cwd=tmp_path,
        env={
            "PYTHONPATH": str(target),
            "CODESTRA_ADAPTER_STATE": str(tmp_path / "runtime-state.sqlite3"),
        },
        text=True,
        capture_output=True,
    )
    assert check.returncode == 0, check.stderr

def test_verifier_rejects_tampering_and_wrong_bindings(tmp_path):
    archive, _ = build(tmp_path, "release.tar.gz")
    command=[sys.executable,str(VERIFY),"--archive",str(archive),"--source-sha",SHA,"--builder-sha","b"*40]
    assert subprocess.run(command).returncode == 0
    assert subprocess.run(command[:-3]+["a"*40,"--builder-sha","b"*40]).returncode != 0
    tampered=tmp_path/"tampered"; tampered.mkdir()
    with tarfile.open(archive,"r:gz") as bundle: bundle.extractall(tampered,filter="data")
    (tampered/"src/codestra_vicidial/app.py").write_text("# tampered\n")
    broken=tmp_path/"broken.tar.gz"
    with tarfile.open(broken,"w:gz") as bundle:
        for path in sorted(tampered.rglob("*")): bundle.add(path,arcname="./"+path.relative_to(tampered).as_posix())
    assert subprocess.run([sys.executable,str(VERIFY),"--archive",str(broken),"--source-sha",SHA,"--builder-sha","b"*40]).returncode != 0

def test_sensitive_files_fail_closed(tmp_path):
    source=tmp_path/"source"; subprocess.run(["cp","-a",str(SOURCE_ROOT),str(source)],check=True); (source/"credentials.json").write_text("synthetic")
    result=subprocess.run([sys.executable,str(SCRIPT),"--source-root",str(source),"--source-sha",SHA,"--builder-sha","b"*40,"--output",str(tmp_path/"x.tar.gz"),"--metadata",str(tmp_path/"x.json")],capture_output=True,text=True)
    assert result.returncode != 0 and "prohibited" in result.stderr

def test_uploaded_artifact_root_checksum_layout(tmp_path):
    archive, _ = build(tmp_path, "codestra-vicidial-adapter.tar.gz")
    sidecar = tmp_path / "codestra-vicidial-adapter.tar.gz.sha256"
    sidecar.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    result = subprocess.run(["sha256sum", "--check", sidecar.name], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_archive_modes_and_bytes_are_independent_of_umask(tmp_path):
    archives = []
    mode_sets = []
    for mask in (0o022, 0o027, 0o077):
        directory = tmp_path / f'{mask:04o}'
        directory.mkdir()
        archive = directory / 'release.tar.gz'
        metadata = directory / 'metadata.json'
        subprocess.run([
            sys.executable, str(SCRIPT), '--source-root', str(SOURCE_ROOT),
            '--source-sha', SHA, '--builder-sha', 'b' * 40,
            '--output', str(archive), '--metadata', str(metadata),
        ], check=True, umask=mask)
        archives.append(archive.read_bytes())
        with tarfile.open(archive, 'r:gz') as bundle:
            members = bundle.getmembers()
            modes = {m.name: m.mode for m in members}
            mode_sets.append(modes)
            for member in members:
                assert member.uid == member.gid == 0
                assert member.mode == 0o755 if member.isdir() else member.mode in {0o644, 0o755}
                assert member.mode & 0o004, member.name
                if member.isdir():
                    assert member.mode & 0o001, member.name
            for name in ('SOURCE_SHA', 'SHA256SUMS', 'SOURCE_RELEASE_MANIFEST.json'):
                assert modes['./' + name] == 0o644
        assert metadata.stat().st_mode & 0o777 == 0o644
        subprocess.run([sys.executable, str(VERIFY), '--archive', str(archive),
                        '--source-sha', SHA, '--builder-sha', 'b' * 40], check=True)
    assert archives[0] == archives[1] == archives[2]
    assert mode_sets[0] == mode_sets[1] == mode_sets[2]


def test_modes_come_from_source_commit_and_are_nonroot_readable(tmp_path):
    import tempfile
    source = tmp_path / 'mode-source'
    source.mkdir()
    for name in ('pyproject.toml', 'src/codestra_vicidial/app.py',
                 'src/codestra_vicidial/internal_calling.py', 'scripts/validate_runtime.py'):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# synthetic permission fixture\n')
    executable = source / 'scripts/validate_runtime.py'
    executable.chmod(0o755)
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    # Ambient filesystem modes deliberately disagree with the committed intent.
    executable.chmod(0o600)
    (source / 'pyproject.toml').chmod(0o777)
    archive = tmp_path / 'modes.tar.gz'
    subprocess.run([sys.executable, str(SCRIPT), '--source-root', str(source),
                    '--source-sha', SHA, '--builder-sha', 'b' * 40,
                    '--output', str(archive), '--metadata', str(tmp_path / 'modes.json')],
                   check=True, umask=0o077)
    with tempfile.TemporaryDirectory(prefix='source-release-readability-', dir='/tmp') as name:
        extracted = Path(name)
        extracted.chmod(0o755)
        with tarfile.open(archive, 'r:gz') as bundle:
            assert bundle.getmember('./scripts/validate_runtime.py').mode == 0o755
            assert bundle.getmember('./pyproject.toml').mode == 0o644
            bundle.extractall(extracted, filter='data')
        options = {'user': 65534, 'group': 65534, 'extra_groups': []} if os.geteuid() == 0 else {}
        subprocess.run(['/usr/bin/python3', '-c',
                        'import os,pathlib,sys; assert os.geteuid()!=0; '
                        'root=pathlib.Path(sys.argv[1]); '
                        '[p.read_bytes() for p in root.rglob("*") if p.is_file()]; '
                        'assert (root/"SOURCE_SHA").read_text().strip()==sys.argv[2]',
                        str(extracted), SHA], check=True, cwd='/tmp', **options)


def test_verifier_rejects_checksum_valid_path_alias(tmp_path):
    import io
    archive, _ = build(tmp_path, 'release.tar.gz')
    alias_archive = tmp_path / 'alias.tar.gz'
    with tarfile.open(archive, 'r:gz') as bundle:
        files = {m.name: (m, bundle.extractfile(m).read() if m.isfile() else None)
                 for m in bundle.getmembers()}
    alias_name = '././pyproject.toml'
    original, payload = files['./pyproject.toml']
    import copy
    alias = copy.copy(original)
    alias.name = alias_name
    files[alias_name] = (alias, payload)
    info, sums = files['./SHA256SUMS']
    sums += (hashlib.sha256(payload).hexdigest() + '  ./pyproject.toml\n').encode()
    info.size = len(sums)
    files['./SHA256SUMS'] = (info, sums)
    info, raw = files['./SOURCE_RELEASE_MANIFEST.json']
    manifest = json.loads(raw)
    manifest['file_count'] += 1
    manifest['checksums_sha256'] = hashlib.sha256(sums).hexdigest()
    raw = (json.dumps(manifest) + '\n').encode()
    info.size = len(raw)
    files['./SOURCE_RELEASE_MANIFEST.json'] = (info, raw)
    with tarfile.open(alias_archive, 'w:gz') as bundle:
        for info, payload in files.values():
            bundle.addfile(info, io.BytesIO(payload) if payload is not None else None)
    result = subprocess.run([sys.executable, str(VERIFY), '--archive', str(alias_archive),
                             '--source-sha', SHA, '--builder-sha', 'b' * 40], capture_output=True, text=True)
    assert result.returncode != 0, 'checksum-valid alias archive must be rejected'
