# M3 AMI signal matrix

This document defines the lifecycle events that the Vicidialer adapter may publish to
Middleware. Every state below must be derived from an AMI signal or from the
controlled call request/response. The adapter must not manufacture a disposition
because an event is missing.

## Identity

The controlled internal originate carries:

- `CODESTRA_CALL_ID`: the stable gateway call identifier.
- `CODESTRA_CORRELATION_ID`: the Odoo/Middleware correlation identifier.

Asterisk channel events are accepted only when those values remain consistent with
the immutable SQLite call row. Published envelopes use the persisted
`correlation_id`; event payloads use the persisted `call_id`. A later event that
changes either value is rejected and is not projected.

## Evidence-backed states

| State | Evidence | Adapter event |
| --- | --- | --- |
| requested | Odoo/Middleware accepted a new idempotent request before dispatch | control-plane record |
| accepted | Middleware/controlled adapter accepted dispatch, or a real channel was created/offered | `created`, `offered` |
| queued | Asterisk placed the caller in a named queue | `QueueCallerJoin` -> `queued` |
| dialing | Asterisk began dialing or reports Dialing/Dialing Offhook | `DialBegin`/ `Newstate` -> `dialing` |
| ringing | Asterisk reports Ring/Ringing | `Newstate` -> `ringing` |
| answered | Asterisk reports answer/agent connect | `DialEnd: ANSWER`/ `AgentConnect`/ `Newstate: Up` -> `answered` |
| connected | The channel is bridged/connected | `BridgeEnter` or the answer transition -> `connected` |
| completed | An answered call has ended and all tracked channels are inactive | `Hangup`/ `AgentComplete` -> `hangup`, then `completed` |
| failed | An explicit unavailable/congestion outcome, or an unclassified pre-answer hangup | `DialEnd: CHANUNAVAIL/CONGESTION` or unclassified `Hangup` -> `failed` |
| busy | Asterisk explicitly reports busy | `DialEnd: BUSY`, `Newstate: Busy`, or cause 17 -> `busy` |
| no_answer | Asterisk reports no answer | `DialEnd: NOANSWER`, `AgentRingNoAnswer`, or cause 18/19 -> `no_answer` |
| canceled | The queue caller explicitly abandons the queue | `QueueCallerAbandon` -> `canceled` |
| rejected | Asterisk supplies an explicit rejection cause | cause 21 or rejection text -> `rejected` |
| timeout | Not emitted by this adapter yet | no authoritative source currently configured |

Queue events are projected only when the queue resolves to exactly one configured
agent binding. An ambiguous queue is quarantined instead of being assigned to an
invented agent.

Generic Hangup cause 16 is not treated as cancellation, no-answer, or timeout.
It is only an end-of-channel signal; the final outcome remains `completed` after
an answer or `failed` before an answer unless a more specific signal is present.

For hangup direction, `hangup_leg` records only whether the AMI Uniqueid matches
the configured agent leg or a known peer leg. It is evidence about the channel
that ended, not proof of a human's local/remote intent. Asterisk Hangup causes do
not by themselves establish who clicked hangup.

## Ordering and delivery

The SQLite gateway outbox is at-least-once and assigns a monotonic sequence per
immutable call. Duplicate AMI fingerprints are idempotent. A lower-ranked delayed
event cannot regress a connected or terminal call. Middleware must apply the same
sequence guard to its durable Odoo read-back projection; a browser disconnect does
not delete or pause the server-side lifecycle.

Authoritative Asterisk references:

- [QueueCallerAbandon](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Events/QueueCallerAbandon/)
- [QueueCallerJoin](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Events/QueueCallerJoin/)
- [AgentRingNoAnswer](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Events/AgentRingNoAnswer/)
- [DialBegin](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Events/DialBegin/)
- [DialEnd](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Events/DialEnd/)
- [Hangup](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Events/Hangup/)
