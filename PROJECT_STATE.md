# Project State

> 이 문서는 저장소에서 확인 가능한 사실과 미확인 사항을 구분해 기록합니다.
> 레이블: **VERIFIED** = GitHub 저장소에서 직접 확인 | **NOT VERIFIED** = 외부 또는 실행 결과 미확인

## 1. Overview (VERIFIED)
한국에서 관심이 커지는 주제와 소비·생활 아이디어를 탐색하는 개인용 트렌드 레이더다.
투자·사업 의사결정 도구로 취급하지 않는다.

## 2. 최신 main 상태 (VERIFIED)
- 최신 main HEAD SHA: `a908bc7bdea732f03298bb4bfd05ff5d5f5d98a1`
- 최신 커밋 메시지: `ORDER02 복구 – cron-job.org 문구 교정 (#4)` (2026-11-13T13:48:13Z)
- 현재 PR #5 브랜치: `docs/order03-project-state`

## 3. 현재 구현 상태

### 3.1 데이터 수집 (VERIFIED)
- RSS 피드: `config/feeds.yaml` 활성 피드 62개, 14개 매체 (README 기준; 작업 전 재확인)
- GitHub Actions 수집 워크플로: `.github/workflows/collect.yml`, 매시간 `17 * * * *` 스케줄
- 보조 호출: 외부 cron-job.org 매시 47분 `workflow_dispatch` 설정 — **현재 활성 여부와 정상 작동 여부는 NOT VERIFIED** (저장소 외부 설정)

### 3.2 데이터 저장 (VERIFIED)
- `data/daily/` — 날짜별 gzip 집계
- `data/days.csv`, `data/health/`, `data/aliases/`
- 헤드라인 원문은 저장하지 않는다.

### 3.3 RSS 감사 (VERIFIED)
- `.github/workflows/audit.yml` 수동 실행, 읽기 전용
- `scripts/audit_feeds.py`, `radar/audit.py`, `config/audit.yaml`

### 3.4 핵심 코드 (VERIFIED)
- `radar/` — 핵심 집계 로직
- `scripts/collect.py` — RSS 수집
- `scripts/status.py` — 일일 상태 보고

### 3.5 테스트 (NOT VERIFIED)
- `tests/` 아래 테스트 파일 존재 확인됨 (VERIFIED)
- 테스트 실행 및 결과: **NOT VERIFIED** — 이 PR에서 테스트를 직접 실행하지 않았다.
- CI 통과 여부: **NOT VERIFIED**

### 3.6 아카이브 및 임시 화면
- 기존 네이버 검색어 대시보드: `archive/v1-naver-search/`
- `docs/index.html`: 임시 안내 화면 (GitHub Pages `docs/` 서비스)

## 4. 미구현 사항
- YouTube Data API 연동: 별도 정책 검토 전 미구현 (VERIFIED — AGENTS.md 명시)
- 외부 cron-job.org 모니터링: 미구현

## 5. 진행 중인 작업
- PR #5: ORDER03 — 프로젝트 문서 체계 도입 (open)
- PR #3: `ci/agents-update-order02` (open — 수정·병합·종료 보류)

## 6. 알려진 위험 (NOT VERIFIED)
- 외부 cron-job.org 호출이 중단될 가능성
- RSS 피드 가용성 변화로 인한 데이터 품질 저하

## 7. 문서 갱신 원칙
- 중요한 변경이 `main`에 병합된 뒤 이 파일을 갱신한다.
- 모든 설계 결정은 `docs/DECISION_LOG.md`에 기록한다.
- 문서와 실제 저장소가 다르면 실제 코드·설정·GitHub 상태를 우선한다.
