# 한국 트렌드 레이더 (무료)

네이버 데이터랩 검색어트렌드를 매일 수집해 GitHub Pages로 보여주는 소비·산업 트렌드 대시보드입니다.
비용은 0원입니다. (네이버 오픈API 무료, GitHub Actions·Pages 무료 — 공개 저장소 기준)

## 세팅 순서 (약 10분)

1. **네이버 API 키 발급** — https://developers.naver.com/apps/#/register 에서 애플리케이션 등록,
   사용 API에 **데이터랩(검색어트렌드)** 선택 → Client ID / Client Secret 확인
2. **GitHub 새 저장소 만들기** (Public 권장) → 이 폴더의 파일을 전부 올리기
3. 저장소 **Settings → Secrets and variables → Actions → New repository secret**
   - `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 두 개 등록
4. **Settings → Pages** → Source: `Deploy from a branch`, Branch: `main`, Folder: `/docs`
5. **Actions 탭 → Update trend data → Run workflow** 로 첫 수집 실행
   (이후 매일 한국시간 07:00에 자동 갱신)
6. 접속 주소: `https://<내계정>.github.io/<저장소이름>/`

## 키워드 바꾸기

`config.json`의 `sections`/`groups`를 수정하면 됩니다.
- 한 섹션에 그룹은 최대 5개 (네이버 API 제한)
- 한 그룹에 키워드는 여러 개 가능 (합산해서 하나의 지수로 계산)

## 로컬에서 미리보기

```bash
python scripts/collect.py --demo      # 가짜 데이터 생성
cd docs && python -m http.server 8000 # http://localhost:8000
```

## 읽는 법과 한계

- 지수는 **섹션 안에서 최고점=100** 으로 정규화된 상대값입니다. 섹션이 다르면 직접 비교할 수 없습니다.
- 급상승 = 최근 7일 평균 vs 직전 7일 평균, 추세 가속 = 최근 7일 vs 직전 4주 평균.
- 검색량은 "관심"의 선행·동행 지표일 뿐 매출이 아닙니다. 지수가 아주 낮은 키워드는 변화율이 크게 튈 수 있습니다.
- 다음 단계 후보: 한국은행 ECOS·통계청 KOSIS 경제지표 섹션, 뉴스 RSS 헤드라인 키워드 집계.
