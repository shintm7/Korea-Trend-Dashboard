# 한국 트렌드 레이더 — 1단계: 뉴스 헤드라인 키워드 수집

언론사 분야별 RSS(정치 제외)에서 헤드라인의 명사와 2~3어절 조합을 뽑아 **키워드별 일일 등장량**을 장기 보관합니다.
헤드라인 원문은 저장하지 않습니다. 네이버 쇼핑과 YouTube는 이번 단계에 포함하지 않습니다.

## 구조

```
config/feeds.yaml        피드 목록 (62개 활성, 14개 매체, 정치·오피니언 제외). enabled 로 켜고 끈다
config/settings.yaml     수집 주기·임계값·완전한 날 기준
config/stopwords.txt     트렌드 신호가 못 되는 일반 단어
config/politics_terms.txt 정치 헤드라인 제외용 단어
radar/                   수집·추출·집계·품질 판정 코드
scripts/collect.py       수집 1회 (Actions가 2시간마다 실행)
scripts/probe_feeds.py   모든 피드의 접근 가능 여부 점검 (수동 실행)
scripts/status.py        수집 현황과 급증 판정 가능 여부
scripts/simulate.py      오프라인 5일 시뮬레이션
data/daily/날짜.csv      키워드별 일일 집계 (장기 보관)   keyword,n,articles,outlets
data/days.csv            날짜별 품질 기록 (완전한 날 판정)
data/health/날짜.csv     피드별 수집 건강 기록
data/aliases/            별칭 이력 (canon.json, alias_map.json, merge_log.csv)
archive/v1-naver-search/ 기존 네이버 검색어 대시보드 (보관용, 실행되지 않음)
logs/                    테스트 결과(test-results.txt)와 시뮬레이션 실행 로그(fixture-run.log)
```

## 시작하는 순서

1. 이 폴더의 내용을 저장소 루트에 올립니다. 기존 루트의 v1 파일(`scripts/`, `config.json`, `docs/`, `.github/workflows/update.yml`)은
   `archive/v1-naver-search/` 와 겹치므로 **루트에서는 지웁니다.** 그래야 v1의 예약 실행이 멈춥니다.
2. Settings → Actions → General → Workflow permissions 를 **Read and write** 로 설정합니다.
3. Actions 탭 → **Probe RSS feeds** → Run workflow. 결과 표(실행 요약)와 `probe-report` 아티팩트가 나옵니다.
   `실패`·`정지 의심` 피드는 `config/feeds.yaml` 에서 `enabled: false` 로 바꿉니다.
4. Actions 탭 → **Collect news keywords** → Run workflow 로 첫 수집을 확인합니다. 이후 2시간마다 자동 실행됩니다.

## 동작 방식

- **날짜 없는 피드**: 한겨레·세계일보 피드는 기사 시각이 없다. 처음 본 제목 해시를 7일간 기억해 이튿날 다시 세지 않는다.
- **시간대 표기 없는 피드**: 한국 시각을 시간대 없이 적는 피드(프레시안·시사저널 등)는 9시간 미래로 읽히므로 자동으로 9시간 되돌린다.
- **수집 주기**: 2시간마다. RSS는 최근 기사 수십 건만 보여 주고, 연예 피드는 3시간 분량뿐이라 하루 한 번으로는 놓칩니다.
- **중복 제거**: 정규화한 제목의 해시로 같은 기사를 한 건으로 셉니다(통신 기사가 여러 매체에 실려도 1건). 매체 수(`outlets`)는 따로 셉니다.
- **키워드**: 어절 안에서 이어진 명사 묶음과 구성 명사, 그리고 이어진 2~3어절의 명사 묶음. 쉼표·말줄임표를 건너뛰는 조합은 만들지 않습니다.
- **정치 제외**: 정치 분야 피드는 목록에서 뺐고, 다른 분야에 섞인 정치 헤드라인은 `politics_terms.txt` 로 한 번 더 거릅니다. 거른 건수는 `days.csv` 에 남습니다.
- **상태**: 수집 중간 상태(해시·키워드·매체)는 git이 아니라 Actions **캐시**에 둡니다. 헤드라인이 git 기록에 영구히 남지 않게 하려는 것입니다.
- **날 확정**: 오늘·어제는 열려 있어 상태에서 매번 다시 계산하고(중복 집계 없음), 이틀 지난 날부터 `data/` 에 파일로 확정해 커밋합니다. 커밋은 하루 한 번 정도입니다.
- **별칭**: 띄어쓰기·대소문자만 다른 표기(`두바이쫀득쿠키` = `두바이 쫀득 쿠키`)는 저장할 때 합칩니다. 줄임말(`두쫀쿠`)은 원자료를 건드리지 않고
  `alias_map.json` 에만 기록하며, 분석할 때 `radar.alias.apply_alias_map()` 으로 합칩니다. 잘못 합쳤으면 그 줄을 지우면 됩니다. 모든 병합은 `merge_log.csv` 에 남습니다.

## 장애를 하락으로 오인하지 않는 장치

날짜마다 `days.csv` 에 아래 기준으로 **완전한 날 / 불완전한 날**을 기록합니다. 불완전한 날은 기준선과 급증 계산에서 뺍니다.

| 플래그 | 뜻 |
|---|---|
| `coverage_low` | 정상 수집한 피드 비율이 70% 미만 (정지 의심 피드는 정상으로 세지 않음) |
| `runs_low` | 하루 6회 미만 실행 (예약 실행 누락) |
| `volume_low` | 기사 수가 최근 완전한 날들의 중앙값의 절반 미만 |
| `state_reset` | 캐시 상태가 사라져 새로 시작함 |

완전한 날이 **21일** 쌓이기 전에는 `scripts/status.py` 가 급증 판정을 "보류"로 표시합니다. robots.txt 가 막은 피드는 장애로 세지 않고 수집에서 제외합니다.

## 이용 조건 관련

- 요청 전에 서버별 `robots.txt` 를 확인하고, 막힌 주소는 가져오지 않습니다. 같은 서버에는 1초 이상 간격을 두고, 변경이 없으면 `304` 로 받습니다.
- 공개하는 것은 키워드별 건수뿐이고 헤드라인·링크·본문은 저장하지 않습니다.
- 언론사별 RSS 이용 약관을 법적으로 검토한 것은 아닙니다. 피드 안의 저작권 문구와 robots.txt 만 확인했습니다.

## 알아둘 점

- 급증 판정은 **아직 구현하지 않았습니다.** 이번 단계는 수집·집계·품질 기록까지이고, 기준선이 쌓인 뒤 다음 단계에서 만듭니다.
- GitHub 예약 실행은 몇 분에서 수십 분 늦을 수 있습니다. 공개 저장소는 60일 동안 활동이 없으면 예약 실행이 꺼지는데, 하루 한 번의 데이터 커밋이 활동으로 인정되는지는 확인하지 못했습니다.
- 캐시를 수동으로 지우면 그 시점까지의 열린 날 데이터가 사라지고 `state_reset` 으로 표시됩니다.
- 로컬 실행: `pip install -r requirements.txt` 후 `python -m unittest discover -s tests`, `python scripts/simulate.py`.
