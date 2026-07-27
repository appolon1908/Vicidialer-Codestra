# Production gate

Recommendation: **STAGING-ONLY / NO-GO for production**.

Passing:

- working telephony preserved;
- MariaDB and AMI are loopback-only;
- baseline and new offline tests pass;
- adapter operations are allowlisted, authenticated, idempotent, audited, and
  read back;
- all mutation flags default false;
- production activation is absent and hard-disabled.

Blocking:

- new adapter has not been deployed through approved private mTLS ingress;
- source-only TCP/9443 firewall rule and rollback have not been exercised;
- MyISAM table snapshot/isolated restore rehearsal is pending;
- live staging synthetic campaign/lead/agent/callback/transfer tests are pending;
- event lifecycle restart/no-gap evidence is pending;
- authoritative Odoo/n8n mapping comparison is pending.

Do not enable production dialing.
