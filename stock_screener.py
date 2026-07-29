"""저평가·반등 스크리너 — 코스피200·코스닥150 중 "상승 여력이 높아 보이는" 종목을
점수제로 랭킹해서 매일 상위 종목을 공시·뉴스·직전 분기 실적(참고용)·보조지표와
함께 텔레그램으로 전송한다.

topic_recommender.py와 완전히 분리된 별도 파이프라인이며, 별도 PythonAnywhere
예약 작업(Tasks)으로 실행하는 것을 전제로 만들었다. 대상 종목이 매일 크게
바뀌지는 않으므로 종목리포트 파이프라인에 있는 "최근 선정 종목 제외" 같은 쿨다운
로직은 쓰지 않는다 — 어제도 상위권이었던 종목이라도 공시·뉴스·기술적 지표는
매일 달라지므로 매번 다시 보여준다.

2026-07-28 설계 변경 이력 (사용자 피드백):
- v1: "52주 고점 대비 -50%↓ + 실적 개선(YoY)"을 AND로 요구 → 거의 항상 0건
- v2: 하락폭 20~50% 구간 + 기술적 강세 신호 하나 이상(하드 필터) → 여전히 0건이
  자주 나왔다(조건 하나만 안 맞아도 전체 0건이 되는 구조적 약점)
- v3(현재): 하드 필터를 완전히 없애고 점수제 랭킹으로 전환.
  - 실적은 분기별 노이즈(계절성·일회성 비용)가 커서 "일시적 적자 = 부실"로 보기
    어렵다는 판단 하에 점수에서 완전히 제외 — 참고정보로만 보여주고 사람이
    직접 보고 거르게 한다.
  - 하락률은 "너무 얕지도 너무 깊지도 않은" 지점(35%)을 정점으로 하는 점수 곡선
    으로 반영 — 하드 컷오프가 아니라 점진적 가중치라 경계값 근처에서 종목이
    통째로 사라지는 문제가 없다.
  - 기술적 신호는 OR 방식(신호 종류 수만큼 가산) — RSI 과매도·MACD 골든크로스·
    이동평균 골든크로스(단기/중기)·볼린저밴드 하단이탈은 서로 겹치는 지표라
    AND로 묶으면 거의 항상 0건이 된다. 여러 개 겹칠수록 고득점.
  - 점수 합산 후 상위 TOP_N만 보여준다 — 유니버스/가격 조회만 정상이면 "오늘은
    없습니다"가 구조적으로 나오지 않는다.

단계:
1) 코스피200+코스닥150 유니버스 조회 (lib/universe.py)
2) 각 종목 현재가+52주 고저 조회 (lib/market_data.py) → 조회 성공한 종목만 채점 대상
3) 조회 성공 종목의 보조지표 계산 (lib/technical_indicators.py)
4) 하락률 점수 + 기술적 신호 점수를 합산해 랭킹, 상위 TOP_N만 선정
5) 선정 종목마다 직전 분기 실적(DART, 참고용)·최근 공시(DART)·뉴스(네이버) 수집
6) 마크다운 리포트 파일 생성 + 텔레그램 요약 메시지·파일 전송
"""

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from lib import dart_client, market_data, stock_discovery, technical_indicators, telegram_client, universe

TOP_N = 15

# 하락률 점수: PRICE_SCORE_PEAK_PCT(%)에서 최고점, 거기서 1%p 멀어질 때마다
# PRICE_SCORE_SLOPE만큼 감점(0 밑으로는 안 내려감). 너무 얕은 조정도, 너무 깊은
# 폭락도 자연스럽게 감점되지만 하드 컷오프처럼 통째로 제외되지는 않는다.
PRICE_SCORE_MAX = 30.0
PRICE_SCORE_PEAK_PCT = 35.0
PRICE_SCORE_SLOPE = 1.0

# 기술적 신호 점수: 강세 신호 "종류" 수 × 가중치 (OR 방식 — 하나만 있어도 인정,
# 여러 종류가 겹칠수록 가산). RSI/골든크로스(단기·중기)/MACD/볼린저밴드 5종 기준
# 전부 겹치면 40점 만점.
TECHNICAL_SIGNAL_WEIGHT = 8.0
TECHNICAL_SCORE_MAX = 40.0

LOGS_DIR = Path(__file__).resolve().parent / "logs"
REPORTS_DIR = Path(__file__).resolve().parent / "screener_reports"


def _log(lines: list[str]) -> None:
    print("\n".join(lines))
    LOGS_DIR.mkdir(exist_ok=True)
    log_path = LOGS_DIR / f"screener-{datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()}.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")


def _price_score(pct_below_high: float) -> float:
    return max(0.0, PRICE_SCORE_MAX - abs(pct_below_high - PRICE_SCORE_PEAK_PCT) * PRICE_SCORE_SLOPE)


def _technical_score(technical: technical_indicators.TechnicalSnapshot) -> float:
    distinct_kinds = {s.kind for s in technical.bullish_signals}
    return min(TECHNICAL_SCORE_MAX, len(distinct_kinds) * TECHNICAL_SIGNAL_WEIGHT)


@dataclass
class RankedCandidate:
    constituent: universe.Constituent
    price_range: market_data.PriceRangeSnapshot
    technical: technical_indicators.TechnicalSnapshot
    price_score: float
    technical_score: float

    @property
    def total_score(self) -> float:
        return self.price_score + self.technical_score


@dataclass
class ScreenedStock:
    name: str
    code: str
    market: str
    price_range: market_data.PriceRangeSnapshot
    technical: technical_indicators.TechnicalSnapshot
    price_score: float
    technical_score: float
    earnings: dart_client.EarningsSnapshot | None
    disclosures: list[dart_client.Disclosure]
    news: list[str]

    @property
    def total_score(self) -> float:
        return self.price_score + self.technical_score


@dataclass
class RankingResult:
    ranked: list[RankedCandidate]
    price_fetch_failed: list[str]  # "종목명(코드)" — 가격/52주 고저 조회 자체가 실패
    technical_fetch_failed: list[str]  # "종목명(코드)" — 보조지표 조회 자체가 실패


def _rank_universe(constituents: list[universe.Constituent]) -> RankingResult:
    price_fetch_failed = []
    technical_fetch_failed = []
    candidates = []

    for c in constituents:
        price_snap = market_data.get_price_and_52w_range(c.code)
        if price_snap is None:
            price_fetch_failed.append(f"{c.name}({c.code})")
            continue

        technical = technical_indicators.analyze(c.code)
        if technical.error:
            technical_fetch_failed.append(f"{c.name}({c.code})")
            continue

        candidates.append(
            RankedCandidate(
                constituent=c,
                price_range=price_snap,
                technical=technical,
                price_score=_price_score(price_snap.pct_below_52w_high),
                technical_score=_technical_score(technical),
            )
        )

    candidates = [cand for cand in candidates if cand.total_score > 0]
    candidates.sort(key=lambda cand: cand.total_score, reverse=True)
    return RankingResult(ranked=candidates, price_fetch_failed=price_fetch_failed, technical_fetch_failed=technical_fetch_failed)


def _enrich(top_candidates: list[RankedCandidate]) -> list[ScreenedStock]:
    result = []
    for cand in top_candidates:
        c = cand.constituent
        corp_code = dart_client.get_corp_code(c.code)
        earnings = dart_client.get_latest_earnings(corp_code) if corp_code else None
        if earnings is None:
            print(f"[스크리너] 실적 조회 실패(참고정보 없이 진행): {c.name}({c.code})")
        disclosures = dart_client.get_recent_disclosures(corp_code) if corp_code else []
        news = stock_discovery.get_stock_news(c.code)
        result.append(
            ScreenedStock(
                name=c.name,
                code=c.code,
                market=c.market,
                price_range=cand.price_range,
                technical=cand.technical,
                price_score=cand.price_score,
                technical_score=cand.technical_score,
                earnings=earnings,
                disclosures=disclosures,
                news=news,
            )
        )
    return result


def _stock_section_markdown(rank: int, s: ScreenedStock) -> str:
    lines = [f"## {rank}위 — {s.name} ({s.code}, {s.market}) — 점수 {s.total_score:.0f}/70", ""]
    lines.append(
        f"- 현재가 {s.price_range.current:,.0f}원 / 52주 최고 {s.price_range.high_52w:,.0f}원 "
        f"→ 고점 대비 **-{s.price_range.pct_below_52w_high:.1f}%** (가격점수 {s.price_score:.0f}/30)"
    )
    lines.append(f"- 직전 분기 실적(참고용, 점수 미반영): {s.earnings.summary_line() if s.earnings else '조회 실패'}")
    lines.append("")

    lines.append("### 최근 공시")
    if s.disclosures:
        for d in s.disclosures:
            lines.append(f"- [{d.receipt_date}] {d.report_name} ({d.url})")
    else:
        lines.append("- 최근 14일 내 공시 없음(또는 조회 실패)")
    lines.append("")

    lines.append("### 최근 뉴스")
    if s.news:
        for n in s.news:
            lines.append(f"- {n}")
    else:
        lines.append("- 조회된 뉴스 없음")
    lines.append("")

    lines.append(f"### 기술적 지표 (기술점수 {s.technical_score:.0f}/40)")
    t = s.technical
    ma_line = ", ".join(f"{w}일선 {v:,.0f}원" for w, v in sorted(t.ma.items()))
    if ma_line:
        lines.append(f"- 이동평균: {ma_line}")
    if t.rsi14 is not None:
        lines.append(f"- RSI(14): {t.rsi14:.1f}")
    if t.macd is not None and t.macd_signal is not None:
        lines.append(f"- MACD: {t.macd:.1f} / 시그널 {t.macd_signal:.1f}")
    if t.bb_upper is not None and t.bb_lower is not None:
        lines.append(f"- 볼린저밴드(20,2): 상단 {t.bb_upper:,.0f}원 / 하단 {t.bb_lower:,.0f}원")
    if t.volume_ratio_20d is not None:
        lines.append(f"- 거래량(20일 평균 대비): {t.volume_ratio_20d * 100:.0f}%")
    for signal in t.bullish_signals:
        lines.append(f"  - 🟢 강세 신호: {signal.text}")
    for signal in t.bearish_signals:
        lines.append(f"  - 🔴 약세 신호(참고): {signal.text}")
    lines.append("")
    return "\n".join(lines)


def _summary_message(screened: list[ScreenedStock], universe_size: int, scored_count: int) -> str:
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"📈 저평가·반등 스크리너 ({today} 기준)",
        f"대상 유니버스 {universe_size}종목 → 점수 산출 가능 {scored_count}종목 → 상위 {len(screened)}종목",
        "",
    ]
    if not screened:
        lines.append("오늘은 점수를 매길 수 있는 종목이 없습니다 (데이터 조회 문제일 가능성 — 로그 확인 필요).")
        return "\n".join(lines)

    for i, s in enumerate(screened, start=1):
        lines.append(f"{i}. [{s.market}] {s.name} — 점수 {s.total_score:.0f} (고점 대비 -{s.price_range.pct_below_52w_high:.1f}%)")
        if s.technical.bullish_signals:
            lines.append(f"   🟢 {s.technical.bullish_signals[0].text}")
        lines.append("")

    lines.append("※ 상세 리포트(공시·뉴스·전체 지표) 파일 첨부")
    return "\n".join(lines)


def main() -> int:
    load_dotenv()
    log_lines = [f"[실행] {datetime.now(ZoneInfo('Asia/Seoul'))}"]

    constituents = universe.get_universe()
    log_lines.append(f"[유니버스] 코스피200+코스닥150 {len(constituents)}종목 조회")

    ranking = _rank_universe(constituents)
    log_lines.append(
        f"[랭킹] 가격조회실패 {len(ranking.price_fetch_failed)}건 / 지표조회실패 {len(ranking.technical_fetch_failed)}건 "
        f"→ 점수 산출 {len(ranking.ranked)}종목"
    )
    if ranking.price_fetch_failed:
        log_lines.append(f"[랭킹][가격조회실패 샘플] {ranking.price_fetch_failed[:10]}")
    if ranking.technical_fetch_failed:
        log_lines.append(f"[랭킹][지표조회실패 샘플] {ranking.technical_fetch_failed[:10]}")
    if constituents and len(ranking.price_fetch_failed) == len(constituents):
        log_lines.append(
            "[랭킹][경고] 전종목 가격조회 실패 — market_data.get_price_and_52w_range의 "
            "셀렉터/정규식이 실제 페이지 구조와 안 맞을 가능성이 높음"
        )

    top_candidates = ranking.ranked[:TOP_N]
    log_lines.append(
        f"[랭킹] 상위 {len(top_candidates)}종목 선정: "
        f"{[(c.constituent.name, round(c.total_score, 1)) for c in top_candidates]}"
    )

    screened = _enrich(top_candidates)

    REPORTS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    report_path = REPORTS_DIR / f"{date_str}-screener.md"
    if screened:
        content = f"# 저평가·반등 스크리너 {date_str}\n\n" + "\n".join(
            _stock_section_markdown(i, s) for i, s in enumerate(screened, start=1)
        )
        report_path.write_text(content, encoding="utf-8")
        log_lines.append(f"[리포트] {report_path.name} 생성")

    summary = _summary_message(screened, len(constituents), len(ranking.ranked))
    telegram_client.send_message(summary)
    if screened:
        telegram_client.send_document(report_path)
    log_lines.append("[텔레그램] 요약 메시지" + (" + 리포트 파일" if screened else "") + " 전송 완료")

    _log(log_lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
