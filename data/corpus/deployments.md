# Deployments and rollback

## Release strategy

Services are released with a blue/green cutover. The new version is deployed
alongside the running one and receives no production traffic until its readiness
probe has passed for sixty consecutive seconds. Traffic is then shifted in one
step, not gradually: a partial shift means two versions writing to the same
datastore at once, and every schema question becomes a question about which
version wrote the row.

The previous version is kept running, idle, for one hour after cutover. That is
the rollback window.

## Rolling back

To roll back, shift traffic to the previous version. Do not redeploy the old
build from source — the artefact that was running is the artefact to return to,
and rebuilding it invites a different result from the same commit.

Rollback is safe within the one-hour window if, and only if, no forward-only
migration ran during the release. If one did, the old version is running against
a schema it does not know about. In that case the rollback is a roll *forward*:
fix the defect and release again.

A rollback does not need an incident to justify it. If a release looks wrong,
roll back first and investigate afterwards. The cost of an unnecessary rollback
is one deploy cycle; the cost of debugging in production is measured in
customers.

## Migrations

Migrations are forward-only and are applied by the service at startup, before it
reports ready. They must be backward-compatible with the previous version for
the length of the rollback window: add a nullable column, backfill it, and only
make it non-nullable in a later release. A migration that drops a column the
previous version still reads will make rollback impossible, which means the
release is unrecoverable the moment it starts.

Never edit a migration that has been applied anywhere. Write another one.
