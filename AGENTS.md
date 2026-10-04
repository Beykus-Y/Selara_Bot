# AGENTS.md

## Branch workflow

- Treat `main` as the stable production branch and `dev` as the integration branch.
- Start feature and fix work from `dev` on a separate task branch; target ordinary pull requests at `dev`.
- Promote tested changes from `dev` to `main` through a pull request.
- Do not push directly to `main` or `dev`; use pull requests and wait for required CI checks to pass.
