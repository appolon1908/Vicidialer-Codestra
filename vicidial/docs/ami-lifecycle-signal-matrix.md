# AMI → Lifecycle-State Signal Matrix

Every state below is backed by a real, observed AMI event field — none are
synthesized or guessed. Terminal states (marked *) set `CallState.terminal`;
once set, `_apply_event`'s own guard (`if state.terminal: return []`) blocks
all further processing for that call, independent of the ordering guard below.

| Lifecycle state | AMI event | Distinguishing field/value | Notes |
|---|---|---|---|
| `created` | first AMI event for a new `call_key` | n/a | Emitted once, `sequence == 0` |
| `dialing` | `AgentCalled`, `DialBegin` | n/a | Previously mislabeled `offered` |
| `ringing` | `Newstate` | `ChannelStateDesc` = `ring`/`ringing` | |
| `answered` → `connected` | `Newstate` (`ChannelStateDesc=up`), `AgentConnect`, `BridgeEnter` | n/a | Both states emitted in sequence via `_append_answer_and_connect` |
| `held` | `Hold` | only if current state is `connected` | |
| `resumed` → `connected` | `Unhold` | only if current state is `held` | |
| `transferring` → `transferred`* | `BlindTransfer`, `AttendedTransfer` | transfer succeeded + resolvable destination | Terminal |
| `timeout`* | `AgentRingNoAnswer` | n/a | Agent-leg ring timeout — distinct from a far-end outcome |
| `busy`* | `DialEnd` (`DialStatus=BUSY`), `Hangup` (`Cause=17`) | | |
| `no_answer`* | `DialEnd` (`DialStatus=NOANSWER`), `Hangup` (`Cause=18` or `19`) | | Previously collapsed into `missed` |
| `rejected`* | `Hangup` (`Cause=21`) | | Previously collapsed into `missed` |
| `canceled`* | `Hangup` (`Cause=16`), pre-answer | agent/local hangup before pickup | |
| `completed`* / `failed`* / `missed`* | `Hangup` (other causes), `DialEnd` (`CHANUNAVAIL`/`CONGESTION`) | | Generic fallback buckets |

## Genuinely absent — not implemented, no real signal exists

`requested`, `accepted`, `queued` — these occur, if at all, before any AMI
event fires (during Middleware's own pre-dial HTTP transaction to this
adapter). No AMI/VICIdial signal distinguishes them. Adding schema values for
them here would be fabrication with nothing that will ever set them.

## Correlation ID

`envelope.correlation_id` = the real Middleware-established `correlation_id`
when this call was originated through a controlled path
(`internal_calling.py`/`external_calling.py`, both of which durably record
`uniqueid → correlation_id` before AMI dispatch, using `ChannelId=<uniqueid>`
on the `Originate` action to force the real Asterisk channel Uniqueid to that
value) — resolved via `CorrelationResolver`. Falls back to the adapter's own
locally-derived `call_id` for calls not originated through those paths (e.g.
ordinary VICIdial-dialer traffic), so the field is never empty.

## Ordering guarantee

`_append_state` rejects any event whose target state has a lower
`_PROGRESSION_RANK` than the call's current state (e.g. a delayed `Ringing`
arriving after `Connected`), absorbing it silently rather than regressing
durable state. This applies to non-terminal states; terminal states remain
solely governed by the `terminal` flag.
