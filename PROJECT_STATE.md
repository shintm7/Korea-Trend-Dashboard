# Project State

## 1. Overview
The Korea Trend Dashboard is a personal trend‑radar that collects RSS news headlines, aggregates daily statistics, and provides a lightweight view of emerging topics in Korea. It is **not** an investment or business decision tool.

## 2. Current Implementation (VERIFIED)
- **Data collection**: RSS feeds defined in `config/feeds.yaml` (62 active feeds) are collected hourly via `.github/workflows/collect.yml` and an auxiliary `workflow_dispatch` call at 47 minutes past each hour (external cron‑job.org – activity not verified).
- **Storage**: Collected data resides in `data/daily/` (gzip archives) and summary CSVs in `data/`. No raw article bodies are stored.
- **Auditing**: Manual read‑only audit workflow defined in `.github/workflows/audit.yml`.
- **Tests**: Test suite under `tests/` passes on latest `main` (SHA `a918cb1eac0829f8a11815660b673d67f2f79e75`).

## 3. Architecture (VERIFIED)
- `radar/` – core data structures and aggregation logic.
- `scripts/collect.py` – fetches RSS feeds.
- `scripts/status.py` – generates daily status reports.
- GitHub Actions automate collection and audit.

## 4. In‑Progress Work (NOT VERIFIED)
- Integration of external cron‑job.org schedule stability.
- Possible future YouTube data collection (design not implemented).

## 5. Risks (NOT VERIFIED)
- External cron‑job.org may stop invoking the workflow.
- RSS feed availability changes could affect data quality.

## 6. Next Steps (PROPOSED)
- Add monitoring for external cron‑job.org execution.
- Document any future feature additions in `docs/DECISION_LOG.md`.

---
*Document update policy*: All design decisions are recorded in `docs/DECISION_LOG.md`. This file is reviewed before any code or workflow changes.
