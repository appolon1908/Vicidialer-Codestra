# Gitleaks review

Five pre-existing generic-key findings were reviewed without printing matched
values and are ignored by exact Gitleaks fingerprints:

- a prose status token in a final report;
- a UUID-form Keycloak external identifier in synthetic identity evidence;
- a synthetic idempotency key in staging acceptance tooling;
- two 16-character redacted fingerprints in the repository's prior
  secret-scan findings report.

None is an authentication credential or usable secret. The ignore file uses
exact path, rule, and line fingerprints; it does not disable a rule or broadly
allow a value pattern. Candidate-tree, history, and staged-diff scans remain
mandatory.
