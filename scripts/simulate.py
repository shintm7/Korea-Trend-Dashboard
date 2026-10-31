#!/usr/bin/env python3
"""네트워크 없이 5일치 수집을 흉내 내어 전체 흐름(수집→중복 제거→확정→품질 판정)을 점검한다.

가상 상황: 매일 12회 수집, 4일째(11-04) 오후에 피드 장애, 쫀득 쿠키 키워드가 날마다 늘어나는 추세,
정치 헤드라인 섞임, 여러 매체가 같은 제목을 싣는 통신 기사.  결과는 logs/ 아래에 남는다.
"""
import datetime as dt
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from helpers import write_feed  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
SIM = ROOT / "logs" / "sim"
PUBS = {"aaa": "경제 연예".split(), "bbb": "경제 연예".split(), "ccc": "경제 연예".split()}
TOPICS = ["편의점 디저트", "삼성전자 HBM4", "AI 에이전트", "전기차 보조금", "아이돌 컴백 쇼케이스",
          "프로야구 포스트시즌", "겨울 패딩 판매", "김장 비용 부담", "해외 여행 수요", "배달 앱 수수료"]
TEMPL = ["{t} 인기 폭발…매출 급증", "{t} 신제품 출시 이어져", "{t} 논란 속 소비자 반응 엇갈려",
         "{t} 시장 전망 밝아", "{t} 가격 인상 소식에 부담 커져", "[단독] {t} 새 소식 (종합)"]
COOKIE_PER_DAY = {0: 2, 1: 5, 2: 10, 3: 18, 4: 26}
ACRONYM_PER_DAY = {0: 0, 1: 0, 2: 3, 3: 6, 4: 9}
TAILS = ["소비자 관심 집중", "업계 촉각", "전문가 분석", "현장 르포", "이용자 급증", "온라인 화제", "주말 인파",
         "재고 부족", "예약 마감", "할인 행사", "후기 폭주", "수요 늘어", "공급 확대", "판매 호조", "가격 비교"]
POLITICS = ["국회 국정감사 여야 공방 격화", "더불어민주당 새 지도부 선출", "대통령실 개편 발표 임박"]


def ts(day: dt.date, hour: float) -> float:
    base = dt.datetime(day.year, day.month, day.day, tzinfo=KST)
    return (base + dt.timedelta(hours=hour)).timestamp()


def make_world(days):
    rnd = random.Random(20261102)
    stories = []                    # (epoch, title, [(pub, section)])
    for i, day in enumerate(days):
        specs = []
        specs += [rnd.choice(TEMPL).format(t=rnd.choice(TOPICS)) + " " + rnd.choice(TAILS) for _ in range(70)]
        specs += [rnd.choice(["{t} 열풍 이어져", "{t} 품절 대란…편의점 줄서기", "{t} 인기에 디저트 시장 들썩"]
                             ).format(t="두바이 쫀득 쿠키") + " " + rnd.choice(TAILS) for _ in range(COOKIE_PER_DAY[i])]
        specs += [rnd.choice(["두쫀쿠 품절 대란 인기", "두쫀쿠 만들기 열풍"]) + " " + rnd.choice(TAILS)
                  for _ in range(ACRONYM_PER_DAY[i])]
        specs += [rnd.choice(POLITICS) for _ in range(8)]
        for title in specs:
            n_pub = rnd.choice([1, 1, 2, 3])               # 2~3이면 통신 기사처럼 같은 제목을 여러 매체가 싣는다
            pubs = rnd.sample(list(PUBS), n_pub)
            sec = rnd.choice(PUBS["aaa"])
            stories.append((ts(day, rnd.uniform(0, 24)), title, [(p, sec) for p in pubs]))
    return stories


def main():
    days = [dt.date(2026, 11, 2) + dt.timedelta(days=i) for i in range(5)]
    world = make_world(days)
    if SIM.exists():
        shutil.rmtree(SIM)
    (SIM / "data").mkdir(parents=True)
    run_times = [ts(d, h + 0.5) for d in days for h in range(0, 24, 2)]
    run_times.append(ts(days[-1] + dt.timedelta(days=1), 1.5))      # 마지막 날이 지난 뒤 한 번 더
    outage_day = days[2]
    fx = Path(tempfile.mkdtemp())
    log = open(ROOT / "logs" / "fixture-run.log", "w", encoding="utf-8")

    def emit(s=""):
        print(s)
        log.write(s + "\n")

    for t in run_times:
        when = dt.datetime.fromtimestamp(t, KST)
        for f in fx.iterdir():
            f.unlink()
        for pub in PUBS:
            for sec, sid in (("경제", "economy"), ("연예", "ent")):
                feed_id = f"{pub}-{sid}"
                items = [(st, title) for st, title, targets in world
                         if t - 6 * 3600 < st <= t and (pub, sec) in targets]
                items.sort(reverse=True)
                write_feed(fx, feed_id, [(title, st) for st, title in items[:50]])
        if when.date() == outage_day and when.hour >= 10:          # 오후 장애: ccc는 503, bbb는 접속 불가
            for sid in ("economy", "ent"):
                (fx / f"ccc-{sid}.xml").unlink()
                (fx / f"ccc-{sid}.http_error").write_text("")
                (fx / f"bbb-{sid}.xml").unlink()
                (fx / f"bbb-{sid}.timeout").write_text("")
        emit(f"--- {when:%Y-%m-%d %H:%M} KST ---")
        r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "collect.py"),
                            "--feeds", str(ROOT / "tests" / "fixtures" / "sim_feeds.yaml"),
                            "--fixtures", str(fx), "--now", when.isoformat(),
                            "--data-dir", str(SIM / "data"), "--state-dir", str(SIM / "state")],
                           capture_output=True, text=True, cwd=ROOT)
        for line in (r.stdout + r.stderr).splitlines():
            emit(line)
    emit("\n=== 최종 현황 (scripts/status.py 와 같은 계산) ===")
    sys.path.insert(0, str(ROOT))
    from radar.quality import baseline_status, read_days
    from radar.util import load_settings
    cfg = load_settings()
    for row in read_days(SIM / "data" / "days.csv"):
        emit(f"{row['date']} 기사 {row['stories']:>4} 정치제외 {row['politics_dropped']:>2} "
             f"키워드 {row['keywords_stored']:>4} 수집률 {row['coverage']} "
             f"{'불완전: ' + row['flags'] if row['incomplete'] == '1' else '완전'}")
    emit(str(baseline_status(SIM / "data" / "days.csv", cfg)))
    log.close()


if __name__ == "__main__":
    main()
