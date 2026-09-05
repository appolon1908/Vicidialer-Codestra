#!/usr/bin/env python3
"""Fail-closed verification of an installer-compatible Server B archive."""
import argparse, hashlib, json, re, tarfile
from pathlib import Path, PurePosixPath
from build_source_release import EXCLUDED, SUFFIXES, SENSITIVE_NAMES
SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED = {"pyproject.toml", "src/codestra_vicidial/app.py", "src/codestra_vicidial/internal_calling.py", "scripts/validate_runtime.py", "SOURCE_SHA", "SHA256SUMS", "SOURCE_RELEASE_MANIFEST.json"}
def main():
    p=argparse.ArgumentParser(); p.add_argument("--archive",type=Path,required=True); p.add_argument("--source-sha",required=True); p.add_argument("--builder-sha",required=True); a=p.parse_args()
    if not SHA.fullmatch(a.source_sha) or not SHA.fullmatch(a.builder_sha): raise ValueError("invalid trusted SHA")
    with tarfile.open(a.archive,"r:gz") as b:
        members=b.getmembers(); names=[]; file_names=set()
        for m in members:
            n=m.name.removeprefix("./"); q=PurePosixPath(n)
            if m.name != "./" + q.as_posix() or n != q.as_posix() or not n or q.is_absolute() or ".." in q.parts or m.issym() or m.islnk() or not (m.isfile() or m.isdir()) or n in names: raise ValueError("unsafe or duplicate archive member")
            if any(part in EXCLUDED or part.endswith(".egg-info") for part in q.parts) or q.suffix.lower() in SUFFIXES or q.name.startswith(".env") or q.name.lower() in SENSITIVE_NAMES:
                raise ValueError("prohibited sensitive or runtime archive member")
            if m.uid != 0 or m.gid != 0 or m.mode not in ({0o755} if m.isdir() else {0o644, 0o755}):
                raise ValueError("unsafe archive ownership or mode")
            if n in {"SOURCE_SHA", "SHA256SUMS", "SOURCE_RELEASE_MANIFEST.json"} and (not m.isfile() or m.mode != 0o644):
                raise ValueError("generated metadata must be a readable ordinary file")
            names.append(n)
            if m.isfile(): file_names.add(n)
        if not REQUIRED <= set(names): raise ValueError("required archive member missing")
        def read(n):
            f=b.extractfile("./"+n)
            if f is None: raise ValueError("regular member missing")
            return f.read()
        if read("SOURCE_SHA") != (a.source_sha+"\n").encode(): raise ValueError("source identity mismatch")
        manifest=json.loads(read("SOURCE_RELEASE_MANIFEST.json"))
        expected={"schema_version":1,"artifact_type":"codestra-vicidial-server-b-source-release","source_repository":"appolon1908-hue/Vicidialer-Codestra","source_sha":a.source_sha,"builder_workflow_sha":a.builder_sha,"archive_root":".","installer_contract":"systemd-python-source-v1"}
        if any(manifest.get(k)!=v for k,v in expected.items()): raise ValueError("manifest binding mismatch")
        raw=read("SHA256SUMS"); checks={}
        for line in raw.decode("ascii").splitlines():
            if not re.fullmatch(r"[0-9a-f]{64}  [^\n]+",line): raise ValueError("malformed checksum entry")
            d,n=line.split("  ",1)
            if n in checks: raise ValueError("duplicate checksum entry")
            checks[n]=d
        expected_names=file_names-{"SHA256SUMS","SOURCE_RELEASE_MANIFEST.json"}
        if set(checks)!=expected_names or manifest.get("file_count")!=len(checks): raise ValueError("member/checksum set mismatch")
        if manifest.get("checksums_sha256")!=hashlib.sha256(raw).hexdigest(): raise ValueError("checksum manifest digest mismatch")
        for n,d in checks.items():
            if hashlib.sha256(read(n)).hexdigest()!=d: raise ValueError("member checksum mismatch")
    print("SOURCE_RELEASE_VERIFICATION=PASS")
if __name__=="__main__": main()
