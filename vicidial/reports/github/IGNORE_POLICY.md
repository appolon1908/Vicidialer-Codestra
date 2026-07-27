# Ignore policy

The existing rules were preserved and expanded for environment files, secret
files, keys, dumps, archives, recordings/audio, backups, spool/runtime/log
state, private evidence, data volumes, caches, dependencies, and build output.

Source, tests, safe fixtures, parameterized migration source, service and proxy
templates, firewall templates, schemas, and documentation remain eligible for
manual staging. `git add -A` is prohibited for the initial commit.
