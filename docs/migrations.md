# Database changes (migrations)

Updates are staged (scripts/rollout.sh). The new version's database changes run first, and then the **running
version carries on, on the changed database**, until the new one has been checked, tried and has taken over. The
old version also stays ready to take the requests back for ten minutes. So every migration must leave a database
that the version before it still works with.

## The rule: add, then remove a release later

In one release, a migration may:
- add tables, columns (nullable, or with a default the database fills in), indexes and views;
- relax things: drop a NOT NULL, widen a type, drop an index or constraint;
- fill in or tidy data, as long as the old code still reads it the same way.

It must not, in the same release as the code change:
- drop or rename a table or column;
- change a column's type, or make it NOT NULL;
- add a NOT NULL column without a server default (the old version's inserts would fail).

To remove or rename something, use two releases:
1. **Expand:** add the new column or table. The code writes both, reads the new one, and stops using the old one.
2. **Contract:** in a later release, once the version before no longer uses it, drop the old one. Mark that line
   with a `# contract:` comment naming the release that stopped using it, for example
   `op.drop_column("bike", "old_name")  # contract: unused since 0.52.0`.

## How it is checked

- `tests/test_migrations_additive.py` reads every migration from `0048` on. It fails on a removing or tightening
  operation in `upgrade()` that has no `# contract:` mark. `downgrade()` may remove anything.
- CI's rollout check (`scripts/ci-rollout.sh`) starts the previous release and updates it to this one in stages,
  with requests running throughout. After the new migrations it runs the full page and endpoint check against the
  **old** version, and every request must be answered.

A failed staged update leaves the added parts in the database. The old version ignores them, and the next attempt
carries on from there. The nightly update's old "undo the database change" step is now needed only when everything
was replaced at once (`deploy.sh --classic`, or the first update with the front door).
