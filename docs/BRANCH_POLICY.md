# Branch policy

## Canonical branch

`main` is the only long-lived canonical branch. It must represent reviewed,
merged source—not an environment deployment pointer.

## Change flow

```text
main
  <- short-lived feature/fix/chore branch
  <- draft pull request
  <- exact-head source and container CI
  <- Codex review on the unchanged head
  <- independent non-owner approval on the unchanged head
  <- protected merge
  <- final artifact build from the protected merged SHA
```

## Required controls

- Never push implementation work directly to `main`.
- Never rebase, amend, or force-push after review begins.
- Any new commit invalidates prior exact-head CI and approval.
- Production identity is the protected merged Git SHA plus the signed immutable
  registry digest; branch names are not deployment authority.
- Build, SBOM, provenance, vulnerability, manifest, OCI, signature and release
  tuple evidence must all name the same exact SHA and image digest.
- Release artifacts upload only after all security and identity gates succeed.
- All checksum sidecars use download-relative portable filenames.

## Branch retention

Imported migration branches are historical lineage until a separate retention
change proves they are reachable from `main`, are not referenced by a tag or
migration map, and have no open pull request. Branch deletion is never bundled
with implementation work.

## Safety

Repository work does not authorize deployment. Dialing, callbacks, recording,
lead publication, WebSocket production traffic, SMS, email and external
delivery remain disabled until separately certified and approved.
