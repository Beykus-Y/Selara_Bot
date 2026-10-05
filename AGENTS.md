# AGENTS.md

## Branch workflow

- Treat `main` as the stable production branch and `dev` as the integration branch.
- Start feature and fix work from `dev` on a separate task branch; target ordinary pull requests at `dev`.
- Promote tested changes from `dev` to `main` through a pull request.
- Do not push directly to `main` or `dev`; use pull requests and wait for required CI checks to pass.

## Development

- Read the relevant code, tests, and docs before editing; follow existing patterns and keep changes focused.
- For schema changes, add an Alembic migration to the correct migration tree and update related tests.
- Run checks relevant to the change and list them in the pull request. Both required CI checks, `backend` and `frontend`, must pass before merge.
- Do not deploy or change repository settings. Merge pull requests only when explicitly asked.
