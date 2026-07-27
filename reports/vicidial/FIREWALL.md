# Firewall evidence

Firewalld and fail2ban were active. Existing rules were inventoried read-only.
SSH TCP/22 and all current telephony listeners were preserved. MariaDB listens
only on `127.0.0.1:3306`; AMI listens only on `127.0.0.1:5038`.

No firewall mutation was made. Deployment must first schedule automatic rollback
and then allow the adapter private TCP/9443 listener only from `65.109.65.169`.
The proposed service binds `10.40.0.2`, not a public wildcard address. A public
listener is prohibited unless exact-source firewalling and mTLS are verified.
