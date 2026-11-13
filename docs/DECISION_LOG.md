# Decision Log

## ADR-001: Project Purpose & Scope
**Date:** 2023-01-15
**Status:** VERIFIED
**Context:** Need a clear definition of the repository's goal.
**Decision:** The Korea Trend Dashboard is a personal trend‑radar for Korean topics, not an investment or business decision tool.
**Rationale:** Provides clarity for contributors and aligns expectations.
**Consequences:** Documentation and user communications must reflect this purpose.

## ADR-002: RSS Collection Design
**Date:** 2023-02-10
**Status:** VERIFIED
**Context:** Collect RSS feeds hourly.
**Decision:** Use GitHub Actions workflow `.github/workflows/collect.yml` with hourly schedule and auxiliary `workflow_dispatch` at 47 minutes past each hour.
**Rationale:** Leverages GitHub CI, no external services required.
**Consequences:** Requires monitoring of external cron‑job.org for reliability (NOT VERIFIED).

## ADR-003: Data Retention Policy
**Date:** 2023-03-05
**Status:** VERIFIED
**Context:** Preserve daily aggregated data while avoiding raw article storage.
**Decision:** Store gzip archives in `data/daily/` and summary CSVs; do not store full article bodies.
**Rationale:** Reduces storage, respects source copyrights.
**Consequences:** Limits ability to re‑process raw content later.

## ADR-004: RSS Audit Read‑Only Policy
**Date:** 2023-04-12
**Status:** VERIFIED
**Context:** Auditing must not modify source data.
**Decision:** Audit workflow `.github/workflows/audit.yml` is manual and read‑only.
**Rationale:** Prevents accidental data changes during audit.
**Consequences:** Audits must be triggered manually.

## ADR-005: YouTube Data Collection (Future)
**Date:** 2023-05-20
**Status:** PROPOSED
**Context:** Potential expansion to include YouTube metrics.
**Decision:** No implementation until explicit approval and policy review.
**Rationale:** Avoids violating YouTube API terms without proper consent.
**Consequences:** Future work must create a new ADR before proceeding.
