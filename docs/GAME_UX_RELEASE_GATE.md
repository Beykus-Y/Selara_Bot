# Games UX 2.0 — release, smoke and rollback gate (GUX-17 completion)

**Status: UNRELEASED / awaiting owner approval.** Created from `dev` baseline `88785b0` on 9 October 2026. This is an **operator checklist, not evidence that any production rollout occurred**. Historic reconciliation of #194 is already in `docs/GAME_UX_RECONCILIATION.md` (#207); do not duplicate its closed PR accounting here.

## 1. Acceptance ownership

| Gate | Evidence required | State |
|---|---|---|
| All pending code PRs | Independent review, green required backend+frontend checks, merge into `dev` one PR at a time, recheck overlap | [ ] |
| Stop/reveal destructive safety | GUX-02 Mini App confirmation parity, stale/repeated/foreign-user check and live WebView interaction | [ ] |
| Zlobcards timers | Last player finishes ahead of timer, vote starts with its own 75-second timeout; fewer than 2 private responses timeout must not silently hang | [ ] |
| Bunker and Mafia | No unrevealed fields on shared board, private role/actions; stale phase+round controls do nothing | [ ] |
| Gacha group workflow | Both Genshin/HSR in group, local/global wallet, info→free/paid→currency→pull→result→permitted sale; friend pressing another user's button is refused | [ ] |
| Automated lifecycle tests | CI covers 8 games and relevant private/group transitions, negative/cross-chat/stale/timer and reward idempotency | [ ] |
| **GUX-16 manual iOS + Android** | Per-game real-client checklist below, screenshots attached as issue/PR evidence | [ ] |
| **Bunker UI redesign approval** | Actual 6/8/12-player shared board screenshots on iOS and Android, owner-approved layout change in separate PR | [ ] |
| Owner release authorization | Explicit `dev → main` approval, post-deploy owner acknowledgment | [ ] |

## 2. Required real-device walkthrough (GUX-16)

For **every game** capture at least: lobby and rules, private DM recovery where applicable, the action phase keyboard, public/hidden information boundary, final scoreboard/roles, rematch and stale old-button behavior.

- [ ] Dice — iOS / Android, 2 and 3 participants, one roll each
- [ ] Quiz — iOS / Android, correct/incorrect answer and old-question callback
- [ ] Spy — iOS / Android, private role/location, public vote, reveal confirmation
- [ ] WhoAmI — iOS / Android, question, four answers, text guess, role secrecy
- [ ] Bredovukha — iOS / Android, category selector, DM lie, truth vote, stale DM
- [ ] Zlobcards — iOS / Android, 1- and 2-white-card selection, early/timeout vote transition, anonymous results
- [ ] Bunker — iOS / Android, **6, 8 and 12** player board screenshots, nine fields, protected private card, eliminated player
- [ ] Mafia — iOS / Android, **4, 6 and 10+ players**, nights, real DM failures, each role action, day votes, confirmation, timer/restore, feed noise
- [ ] Genshin and HSR Gacha — iOS / Android, in-group banner and wallet scope, subscription, first free pull, insufficient coins, paid pull, sale, duplicate/old control

Tick an item only with real-client evidence. Automated board keyboard simulations or unit tests do **not** count as device smoke.

## 3. Before promoting dev → main

1. Every linked fix PR must be individually reviewed and merged into `dev`; check `docs/GAME_UX_PHASE_MATRIX.md` §5 deviations D1–D4 and update that table **only after fixes are merged**.
2. Run full CI on the **current** `dev` SHA; capture exact SHA and all required workflow statuses in #194. Do not infer readiness from a previously green branch run.
3. Validate migrations on a disposable database and take a fresh production backup. Verify restore viability; do not proceed if the recovery path is unknown.
4. Complete the mobile GUX-16 checklist, acknowledge all deliberate product decisions and mark unverified items as **open**, not done.
5. Request an explicit `dev → main` release command from the owner. Do not merge or deploy based solely on this checklist.

## 4. Immutable deployment and rollback

Follow **existing** `docs/IMMUTABLE_DEPLOY.md` and workflow `.github/workflows/deploy-vps.yml`; don't improvise tags or manually deploy an unreviewed branch:

1. After approved `main` promotion and its successful CI, use a successful **Publish Docker Image** publisher run with a verified `release-manifest` (image digests).
2. Launch **Deploy To VPS** with that exact numeric `release_run_id`; the workflow verifies successful publication from `main` before SSH.
3. Check container image IDs/RepoDigests, `/readyz`, public frontend readiness and game runtime before declaring success.
4. Manually smoke one group game, one private-role recovery flow, and both group Gacha banners after deploy. Monitor errors, timer/recovery alerts and real group interactions.
5. **Rollback:** use a prior publisher run ID if its manifest is available or VPS `.selara-releases/previous.json` with `python3 scripts/release_manifest.py deploy previous` as documented. **DB schema is not automatically rolled back**; verify older version compatibility before switching. Document incident details, the deployed publisher ID and follow-up PR.

## 5. Changelog and evidence register

Before release, write one audience-friendly Gacha + Games changelog, separating:
- new/fixed user-facing behavior by game, with no mechanics/balance claims where unchanged;
- group vs private location of actions and `/gameboard`, `/role`, `/help` recovery shortcuts;
- precautions: old inline buttons require refreshing the board; enabled/disabled features;
- known limitations and device/test coverage which remains incomplete.

On #194 add final `dev` SHA, release PR link, publisher `run_id`, deploy run and digest evidence, linked iOS/Android screenshot references, smoke outcomes and any rollback event. Never claim `main` deployed just because PRs merged into `dev`.
