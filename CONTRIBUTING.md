# Contributing

Small project, simple rules. Adding a whole new area? See [contributing a module](docs/contributing-a-module.md).

1. **Branch and PR.** `main` is protected: nothing is pushed to it directly ([docs/repo-settings.md](docs/repo-settings.md)). Work on a branch; CI (lint, tests, image build, secret scan, dependency
   review, a whole-stack check on PostGIS, and a staged update from the previous release) must pass before
   merging to `main`. Fork pull requests
   are welcome; CI runs on them without access to any secret.
2. **Tests.** New behaviour comes with tests. Database tests use `WA_TEST_DATABASE_URL`
   (any scratch Postgres; PostGIS is not required by the tests).
3. **Dependencies.** Edit `pyproject.toml`, then regenerate both locks with hashes:
   `uv pip compile pyproject.toml --generate-hashes -o requirements.lock` and
   `uv pip compile pyproject.toml --extra dev --generate-hashes -o requirements-dev.lock`.
   The image installs only from `requirements.lock`.
4. **Migrations.** Add a new Alembic revision; never edit one that has been released. Migrations only add:
   removing or renaming takes two releases, because the running version keeps working on the new database
   during an update ([docs/migrations.md](docs/migrations.md)).
5. **Endpoints.** After adding or changing one, run `python scripts/api-doc.py` to update
   [docs/api.md](docs/api.md) (a test checks it).
6. **Third-party data.** Never commit raw feed captures (`samples/` is ignored). Fixtures in
   tests must be small and hand-written.
7. **Releases.** Update `CHANGELOG.md`, merge, then run the Release workflow from the Actions tab
   with the version. The release is gated: it refuses unless CI has passed on the tip of `main`. If you
   change `docker-compose.yml` or a script in a way an older server cannot cope with, increase
   `COMPOSE_REVISION`.
8. **Style.** `ruff check .` clean. Comments explain why, not what.
9. **No personal data.** Tests and examples use made-up or central-London places (for example
   51.5074, -0.1278), never real home coordinates, postcodes or addresses.
