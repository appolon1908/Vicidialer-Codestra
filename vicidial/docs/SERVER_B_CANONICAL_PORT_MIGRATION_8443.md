# Server B canonical port migration through 8443

Status: proposed for architecture review. Runtime remains unchanged during the pre-final baseline soak.

| Port | Bind/owner | Purpose and clients | Security | Proposed disposition |
|---|---|---|---|---|
| 8443 | `10.40.0.2`, Apache | Private restricted VICIdial gateway used by Server A | mTLS, private-source ACL, route-specific HMAC | Candidate canonical integration gateway |
| 8444 | no current listener | Former endpoint-6101 staging service | N/A | Retired; must remain absent |
| 9443 | `10.40.0.2`, Apache | Historical read-only adapter and one staging credential proxy dependency | mTLS; public firewall denial | Temporary compatibility until the dependent path is tested on 8443 |
| 9444 | `10.40.0.2`, `codestra-vicidial-observer.service` | Restricted controller/observer API used by Server A | Direct mTLS and scoped approval token | Separately justified observer until reviewed identity propagation through Apache exists |

The services are not interchangeable. Port 8443 may become the common externally visible private gateway only after Apache can preserve the observer's verified controller SPIFFE identity without trusting caller-supplied headers. Until that implementation is reviewed and tested, 9444 remains a separately justified private observer endpoint rather than being closed merely because 8443 is healthy.

## Migration sequence

1. Preserve all delivery/dialing flags as false and verify zero calls/channels/queue/agents.
2. Issue the observer `serverAuth` certificate from the approved production CA and validate direct 9444 authentication.
3. Add an Apache 8443 observer route that derives identity exclusively from the verified client certificate and strips inbound identity headers.
4. Test valid production identity plus test-CA, expired, revoked, wrong-identity, wrong-scope, HMAC, timestamp and replay negatives.
5. Migrate Server A staging controller configuration to 8443 and observe bounded compatibility traffic.
6. Remove the 9443 staging credential proxy dependency and stop the 9443 vhost after zero usage is proven.
7. Retire 9444 only after all observer clients use 8443 and equivalent end-to-end identity enforcement is proven.
8. Keep public denial for all four ports throughout. Roll back by restoring versioned Apache/controller configuration and the private firewall set.

No listener retirement or runtime migration is authorized by this document alone; protected-main approval and the post-baseline maintenance gate remain required.
