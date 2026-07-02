# Production Sentinel Flare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize Sentinel Flare into a working production-oriented alerting bot and dashboard.

**Architecture:** Keep FastAPI and React/Vite, but move core trading-intelligence logic into focused Python services under `darkpool/`. Repair the frontend by selectively integrating the complete older dashboard branch into the newer view architecture. Keep external data optional and provide deterministic local demo data for tests and offline use.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest, React 18, Vite, Recharts, Tailwind, discord.py.

---

### Task 1: Backend Failure Tests

**Files:**
- Create: `tests/test_app_smoke.py`
- Create: `tests/test_finra_helper.py`
- Create: `tests/test_options_endpoints.py`

- [ ] Write failing import and smoke tests that reproduce missing dependencies, FINRA helper annotation errors, and invalid `dataGenerator` imports.
- [ ] Run the targeted tests and confirm they fail for the current reasons.

### Task 2: Core Services

**Files:**
- Create: `darkpool/__init__.py`
- Create: `darkpool/models.py`
- Create: `darkpool/fixtures.py`
- Create: `darkpool/level_engine.py`
- Create: `darkpool/confluence.py`
- Create: `darkpool/alerting.py`
- Modify: `requirements.txt`

- [ ] Add typed data models and deterministic MAG7 sample data.
- [ ] Implement price-level clustering, freshness, strength, and confluence scoring.
- [ ] Add alert generation and deduplication helpers.
- [ ] Add tests for every scoring and alert behavior before implementation.

### Task 3: Provider and API Repair

**Files:**
- Create: `darkpool/providers.py`
- Modify: `finra_helper.py`
- Modify: `server.py`
- Test: `tests/test_providers.py`
- Test: `tests/test_routes.py`

- [ ] Fix `finra_helper.py` importability.
- [ ] Add provider abstractions with demo fallback.
- [ ] Replace Python imports from JS `dataGenerator` with `darkpool.fixtures`.
- [ ] Add production endpoints for levels, confluence, alert candidates, and watchlist-safe sample data.
- [ ] Keep old route paths compatible where practical.

### Task 4: Discord Bot Repair

**Files:**
- Modify: `discord_bot.py`
- Test: `tests/test_discord_bot.py`

- [ ] Refactor Discord commands to call shared services.
- [ ] Make no-token startup exit cleanly.
- [ ] Add command formatting tests without connecting to Discord.

### Task 5: Frontend Repair and Selective Branch Integration

**Files:**
- Modify: `src/App.jsx`
- Modify: `src/dataGenerator.js`
- Create: `src/flowEngine.js`
- Test: `src/flowEngine.test.js`
- Modify: `package.json`

- [ ] Recover complete dashboard JSX from `origin/codex/improve-darkpool-bot-code`.
- [ ] Preserve newer Settings, Options, Production, and Advanced views.
- [ ] Add pure frontend scoring/export tests where possible.
- [ ] Make `npm run build` pass.

### Task 6: Documentation and Verification

**Files:**
- Modify: `README.md`
- Modify: `.env.example`

- [ ] Document setup, env vars, provider modes, endpoints, Discord commands, testing, and data limitations.
- [ ] Run `pytest`, `npm test` if configured, `npm run build`, backend import checks, and route smoke checks.
- [ ] Remove generated caches and keep git status intentional.
