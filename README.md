# Vicidialer Codestra

Dedicated, history-preserving extraction of the Codestra VICIdial integration work.

The repository contains the restricted VICIdial adapter, campaign-provisioning gateway,
deployment and operations definitions, container packaging, CI/security checks, tests,
release manifests, and the retained VICIdial development/release branches.

The `main` branch is initialized from `fix/vicidial-r3-phase3-remediation`.
Historical workflow revisions are retained under `ci/workflow-history/`; the current
active GitHub Actions workflows are installed after the history import completes.

Production deployment remains governed separately by protected review, immutable image
digest, signature verification, host/database preflight, and rollback controls.
