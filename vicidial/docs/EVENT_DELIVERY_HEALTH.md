# Event delivery health

The event-only AMI gateway writes its local status atomically to
`AMI_EVENT_STATUS_FILE` (default
`/run/codestra-vicidial/ami-events-status.json`). A connected AMI stream
does not establish that Middleware has accepted its events.

The status includes these additive fields within schema version 1.0:

| Field | Meaning |
| --- | --- |
| `pending_events` | Durable pending and retrying events, including leased events. |
| `retrying_events` | Events awaiting another delivery attempt after a failure. |
| `dead_letter_events` | Retained events requiring operator reconciliation after permanent denial or exhausted retries. |
| `delivery_status` | `dead_letter`, `retrying`, `pending`, or `idle`, in that precedence. |

Each status publication reads these counts together from SQLite while holding
the status writer lock, so an older AMI snapshot cannot overwrite a newer
delivery-failure snapshot. Delivered events are
excluded. Counts are refreshed during delivery cycles and AMI state updates;
they survive a gateway restart because they derive from the durable outbox.
No event payload, call identifier, credential, or remote error text is written
into these health fields.

When AMI would otherwise be ready, any retrying or dead-letter event makes the
overall `status` equal `degraded`. New AMI messages cannot clear that condition.
Successful retries restore `ready` on a subsequent delivery cycle without
requiring another AMI message. Delivery recovery does not clear an AMI
disconnection or override process states such as `failed` or `stopped`.

A normal pending queue does not immediately degrade readiness. Monitoring
should also check `observed_at`, `last_delivery_cycle_at`,
`delivery_worker_running`, and the existing backlog limit. A stale status file
is not proof of health, and `idle` alone is not proof of end-to-end acceptance.

For dead letters, investigate the canonical Middleware endpoint, service
identity and scope, signature configuration, and bounded acceptance response.
Preserve event IDs and audit evidence during reconciliation. This change
does not add an automatic dead-letter replay operation or activate dialing.
