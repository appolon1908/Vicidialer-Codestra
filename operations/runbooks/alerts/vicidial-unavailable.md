# VICIdial is unavailable

Alert name: `CodestraVicidialUnavailable`
Escalation-owner reference: `monitoring-owner-registry` (resolve through the approved protected owner record; never place personal contact information here).

## 1. Alert meaning

Prometheus observed `codestra_service_up{service="vicidial"} == 0` continuously for `2m`. The candidate critical threshold is `service_up = 0` and requires post-deployment tuning against measured production baselines.

## 2. Immediate safety action

Keep EXTERNAL_DIAL_ENABLED=false and TRANSFERS_ENABLED=false; isolate signaling/media checks from any carrier dial path.

## 3. Impact

The `vicidial` service may be unavailable, unsafe, degraded, or unable to satisfy its recovery objective. Treat the metric as an indication to verify, not as authorization to activate traffic.

## 4. Verification commands

Run read-only checks from the approved administration path:

```sh
curl --fail --silent --show-error http://127.0.0.1:9090/api/v1/alerts
curl --fail --silent --show-error 'http://127.0.0.1:9090/api/v1/query?query=CodestraVicidialUnavailable'
docker ps --filter label=com.docker.compose.project=codestra-monitoring
journalctl --since '-30 min' --no-pager --output=short-iso
```

Filter output before preservation; do not print environment files, headers, tokens, payloads, telephone numbers, or recordings.

## 5. Likely causes

Exporter or probe failure; dependency outage; capacity exhaustion; credential expiry; configuration drift; network/TLS failure; a failed deployment; or a genuine safety-control violation.

## 6. Staging versus production

This rule is scoped to `production`. Staging alerts may use isolated test routes only. Production alerts must use approved production routes only. Never promote a staging result, receiver, credential, or threshold as production-proven.

## 7. Recovery procedure

1. Preserve alert, metric, relevant redacted logs, config/image digests, and timestamps.
2. Confirm the signal through a second read-only source.
3. Correct the smallest identified cause without changing safety flags.
4. For queues, reconcile metadata read-only and leave quarantined records untouched.
5. Observe the metric below threshold for at least `2m` and verify the resolved notification through the approved route.

## 8. Rollback procedure

Restore the last approved non-secret monitoring configuration or application release using its recorded digest. Validate configuration before reload. If rollback would enable delivery, dialing, workflows, or writes, stop and invoke the external approval process.

## 9. Emergency-disable conditions

Disable or isolate only the affected application path when safety flags change unexpectedly, DNC state is uncertain, unauthorized registration/call activity appears, data integrity is in doubt, or recovery increases impact. Do not disable monitoring evidence collection unless it is the direct cause.

## 10. Expected resolved signal

The alert is absent from `/api/v1/alerts`, its expression remains false for `2m`, and Alertmanager records a resolved notification after receiver recovery without claiming false delivery.

## 11. Evidence to preserve

Alert fingerprint and labels; query result; UTC first/last timestamps; redacted Prometheus/Alertmanager/application logs; config and image digests; operator actions; rollback decision; resolved-delivery evidence. Preserve no secrets or personal contact details.

## 12. Escalation-owner reference

`monitoring-owner-registry`. Resolution is fail-closed when the protected owner record or escalation policy is missing.

## 13. Actions that must not be performed

Do not enable calls, callbacks, transfers, external dialing, production workflows, queue delivery, or Odoo/VICIdial writes. Do not expose credentials or personal contact data. Never place an uncontrolled test call or register an unapproved endpoint.
