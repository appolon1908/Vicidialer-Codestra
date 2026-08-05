# VICIdial core

Live inventory at Section 2 entry contained 25 campaigns, 22 lists, 19 user
groups, 31 inbound groups, 12 scripts, 86 campaign dispositions, 44 users, 40
lead dialing copies and two callbacks. These are operational copies; Odoo
through middleware remains authoritative for customers, leads, activities,
dispositions, callbacks, recording references and follow-up.

Every observed campaign used `MANUAL` dial method and `auto_dial_level=0`.
Existing active flags were recorded but not changed. New resources created by
the adapter are forced inactive, collision-checked, idempotent and read back.
Production activation is hard-disabled.

