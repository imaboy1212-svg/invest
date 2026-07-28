"""저평가 스크리너 — 코스피200·코스닥150 중 52주 고점 대비 20~50% 하락(너무 깊게
무너진 -50% 이상은 제외)했으면서 기술적 지표(RSI/MACD/이동평균 크로스/볼린저밴드)로
저평가·반등 가능성이 확인되는 종목을 매일 찾아, 공시·뉴스·직전 분기 실적과 함께
텔레그램으로 전송한다.

topic_recommender.py와 완전히 분리된 별도 파이프라인이며, 별도 PythonAnywhere
예약 작업(Tasks)으로 실행하는 것을 전제로 만들었다. 대상 종목이 매일 크게
바뀌지는 않으므로 종목리포트 파이프라인에 있는 "최근 선정 종목 제외" 같은 쿨다운
로직은 쓰지 않는다 — 어제도 조건을 만족한 종목이라도 공시·뉴스·기술적 지표는
매일 달라지므로 매번 다시 보여준다.

2026-07-28 사용자 피드백으로 필터 기준을 재설계함 (최초 버전은 "52주 고점 대비
-50%↓ + 실적 개선"을 AND로 요구해 거의 항상 0건이었다):
- 실적은 "전년 대비 개선" 같은 엄격한 필수 조건이 아니라, 직전(최근 확정) 분기
  실적을 참고 정보로만 보여준다.
- 52주 고점 대비 -50% 이상 폭락한 종목은 오히려 제외한다 (반등보다 구조적 부실
  가능성에 무게를 둠). 대신 -20%~-50% 사이의 "적당히 눌린" 종목만 본다.
- 실제 저평가·반등 가능성 판단은 기술적 지표에 맡긴다 — RSI 과매도, 골든크로스,
  볼린저밴드 하단 이탈 등 강세(bullish) 신호가 하나라도 있는 종목만 최종 통과.

단계:
1) 코스피200+코스닥150 유니버스 조회 (lib/universe.py)
2) 각 종목 현재가+52주 고저 조회 → 고점 대비 20~50% 하락 구간만 1차 통과 (lib/market_data.py)
3) 1차 통과 종목의 보조지표 계산 → 강세 신호(bullish) 있는 종목만 2차 통과
   (lib/technical_indicators.py)
4) 2차 통과 종목마다 직전 분기 실적(DART, 참고용)·최근 공시(DART)·뉴스(네이버) 수집
5) 마크다운 리포트 파일 생성 + 텔레그램 요약 메시지·파일 전송
"""

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from lib import dart_client, market_data, stock_discovery, technical_indicators, telegram_client, universe

# 너무 얕은 조정(<20%)은 "저평가"로 보기 어렵고, 너무 깊은 폭락(>=50%)은 반등보다
# 구조적 부실 가능성에 무게를 둬 오히려 제외한다.
DECLINE_MIN_PCT = 20.0
DECLINE_MAX_PCT = 50.0

LOGS_DIR = Path(__file__).resolve().parent / "logs"
REPORTS_DIR = Path(__file__).resolve().parent / "screener_reports"


def _log(lines: list[str]) -> None:
    print("\n".join(lines))
    LOGS_DIR.mkdir(exist_ok=True)
    log_path = LOGS_DIR / f"screener-{datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()}.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")


@dataclass
class ScreenedStock:
    name: str
    code: str
    market: str
    price_range: market_data.PriceRangeSnapshot
    technical: technical_indicators.TechnicalSnapshot
    earnings: dart_client.EarningsSnapshot | None
    disclosures: list[dart_client.Disclosure]
    news: list[str]


@dataclass
class PriceScreenResult:
    passed: list[tuple[universe.Constituent, market_data.PriceRangeSnapshot]]
    fetch_failed: list[str]  # "종목명(코드)" 목록 — 52주 고저/현재가 조회 자체가 실패한 종목
    out_of_range: int  # 조회는 성공했지만 20~50% 구간 밖인 종목 수


def _screen_by_price(constituents: list[universe.Constituent]) -> PriceScreenResult:
    passed = []
    fetch_failed = []
    out_of_range = 0
    for c in constituents:
        snap = market_data.get_price_and_52w_range(c.code)
        if snap is None:
            fetch_failed.append(f"{c.name}({c.code})")
            continue
        if DECLINE_MIN_PCT <= snap.pct_below_52w_high < DECLINE_MAX_PCT:
            passed.append((c, snap))
        else:
            out_of_range += 1
    return PriceScreenResult(passed=passed, fetch_failed=fetch_failed, out_of_range=out_of_range)


def _screen_by_technical(
    price_passed: list[tuple[universe.Constituent, market_data.PriceRangeSnapshot]],
) -> list[tuple[universe.Constituent, market_data.PriceRangeSnapshot, technical_indicators.TechnicalSnapshot]]:
    passed = []
    for c, price_snap in price_passed:
        technical = technical_indicators.analyze(c.code)
        if technical.error:
            print(f"[스크리너] 보조지표 조회 실패로 제외: {c.name}({c.code}) — {technical.error}")
            continue
        if technical.has_rebound_signal:
            passed.append((c, price_snap, technical))
    return passed


def _enrich(
    technical_passed: list[tuple[universe.Constituent, market_data.PriceRangeSnapshot, technical_indicators.TechnicalSnapshot]],
) -> list[ScreenedStock]:
    result = []
    for c, price_snap, technical in technical_passed:
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
                price_range=price_snap,
                technical=technical,
                earnings=earnings,
                disclosures=disclosures,
                news=news,
            )
        )
    return result


def _stock_section_markdown(s: ScreenedStock) -> str:
    lines = [f"## {s.name} ({s.code}, {s.market})", ""]
    lines.append(
        f"- 현재가 {s.price_range.current:,.0f}원 / 52주 최고 {s.price_range.high_52w:,.0f}원 "
        f"→ 고점 대비 **-{s.price_range.pct_below_52w_high:.1f}%**"
    )
    lines.append(f"- 직전 분기 실적: {s.earnings.summary_line() if s.earnings else '조회 실패(참고정보 없음)'}")
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

    lines.append("### 기술적 지표 (저평가·반등 신호)")
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


def _summary_message(screened: list[ScreenedStock], universe_size: int, price_passed_count: int) -> str:
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"📉 저평가·반등 스크리너 ({today} 기준)",
        f"대상 유니버스 {universe_size}종목 → 52주 고점 대비 -{DECLINE_MIN_PCT:.0f}~{DECLINE_MAX_PCT:.0f}% "
        f"구간 {price_passed_count}종목 → 기술적 반등 신호 확인 {len(screened)}종목",
        "",
    ]
    if not screened:
        lines.append("오늘은 조건을 만족하는 종목이 없습니다.")
        return "\n".join(lines)

    for s in screened:
        lines.append(f"[{s.market}] {s.name} — 고점 대비 -{s.price_range.pct_below_52w_high:.1f}%")
        if s.earnings:
            lines.append(f"  {s.earnings.summary_line()}")
        if s.technical.bullish_signals:
            lines.append(f"  🟢 {s.technical.bullish_signals[0].text}")
        lines.append("")

    lines.append("※ 상세 리포트(공시·뉴스·전체 지표) 파일 첨부")
    return "\n".join(lines)


def main() -> int:
    load_dotenv()
    log_lines = [f"[실행] {datetime.now(ZoneInfo('Asia/Seoul'))}"]

    constituents = universe.get_universe()
    log_lines.append(f"[유니버스] 코스피200+코스닥150 {len(constituents)}종목 조회")

    price_screen = _screen_by_price(constituents)
    price_passed = price_screen.passed
    log_lines.append(
        f"[1차:가격] 조회성공 {len(constituents) - len(price_screen.fetch_failed)}/{len(constituents)}종목 "
        f"(조회실패 {len(price_screen.fetch_failed)}건) → 구간 밖 제외 {price_screen.out_of_range}건 "
        f"→ -{DECLINE_MIN_PCT:.0f}~{DECLINE_MAX_PCT:.0f}% 구간 통과 {len(price_passed)}종목: "
        f"{[c.name for c, _ in price_passed]}"
    )
    if price_screen.fetch_failed:
        log_lines.append(f"[1차:가격][조회실패 샘플] {price_screen.fetch_failed[:10]}")
    if len(constituents) > 0 and len(price_screen.fetch_failed) == len(constituents):
        log_lines.append(
            "[1차:가격][경고] 전종목 조회 실패 — market_data.get_price_and_52w_range의 "
            "셀렉터/정규식이 실제 페이지 구조와 안 맞을 가능성이 높음 (구간 기준 문제 아님)"
        )

    technical_passed = _screen_by_technical(price_passed)
    log_lines.append(
        f"[2차:기술적지표] 강세 신호 확인 {len(technical_passed)}종목: "
        f"{[c.name for c, _, _ in technical_passed]}"
    )

    screened = _enrich(technical_passed)
    log_lines.append(f"[3차:정보보강] 최종 {len(screened)}종목 리포트 생성")

    REPORTS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    report_path = REPORTS_DIR / f"{date_str}-screener.md"
    if screened:
        content = f"# 저평가·반등 스크리너 {date_str}\n\n" + "\n".join(_stock_section_markdown(s) for s in screened)
        report_path.write_text(content, encoding="utf-8")
        log_lines.append(f"[리포트] {report_path.name} 생성")

    summary = _summary_message(screened, len(constituents), len(price_passed))
    telegram_client.send_message(summary)
    if screened:
        telegram_client.send_document(report_path)
    log_lines.append("[텔레그램] 요약 메시지" + (" + 리포트 파일" if screened else "") + " 전송 완료")

    _log(log_lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
