"""저평가 스크리너 — 코스피200·코스닥150 중 52주 고점 대비 -50% 이상 하락했지만
실적은 개선된 종목을 매일 찾아 공시·뉴스·보조지표와 함께 텔레그램으로 전송한다.

topic_recommender.py와 완전히 분리된 별도 파이프라인이며, 별도 PythonAnywhere
예약 작업(Tasks)으로 실행하는 것을 전제로 만들었다. 대상 종목이 매일 크게
바뀌지는 않으므로(52주 고점·실적 개선 여부는 하루 만에 잘 안 바뀜) 종목리포트
파이프라인에 있는 "최근 선정 종목 제외" 같은 쿨다운 로직은 쓰지 않는다 — 어제도
조건을 만족한 종목이라도 공시·뉴스·보조지표는 매일 달라지므로 매번 다시 보여준다.

단계:
1) 코스피200+코스닥150 유니버스 조회 (lib/universe.py)
2) 각 종목 현재가+52주 고저 조회 → 고점 대비 -50% 이상만 1차 통과 (lib/market_data.py)
3) 1차 통과 종목의 DART 고유번호 확인 → 최근 실적(YoY) 조회, 개선 종목만 최종 통과
   (lib/dart_client.py)
4) 최종 통과 종목마다 최근 공시(DART)·뉴스(네이버)·보조지표(lib/technical_indicators.py) 수집
5) 마크다운 리포트 파일 생성 + 텔레그램 요약 메시지·파일 전송

주의: 이 스크립트는 코딩 시점의 실행 환경(클라우드 샌드박스)에서 외부 네트워크
접근이 프록시 정책으로 차단되어 있어 직접 실행 검증을 하지 못했다. PythonAnywhere
등 실제 운영 환경에서 첫 실행 후 로그(logs/screener-YYYY-MM-DD.log)를 반드시
확인할 것 — 특히 코스닥150 유니버스 조회, DART 실적 계정명 매칭 부분은 실제
응답 구조에 따라 조정이 필요할 수 있다.
"""

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from lib import dart_client, market_data, stock_discovery, technical_indicators, telegram_client, universe

DECLINE_THRESHOLD_PCT = 50.0
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
    earnings: dart_client.EarningsSnapshot
    disclosures: list[dart_client.Disclosure]
    news: list[str]
    technical: technical_indicators.TechnicalSnapshot


def _screen_by_price(constituents: list[universe.Constituent]) -> list[tuple[universe.Constituent, market_data.PriceRangeSnapshot]]:
    passed = []
    for c in constituents:
        snap = market_data.get_price_and_52w_range(c.code)
        if snap is None:
            continue
        if snap.pct_below_52w_high >= DECLINE_THRESHOLD_PCT:
            passed.append((c, snap))
    return passed


def _screen_by_earnings(
    price_passed: list[tuple[universe.Constituent, market_data.PriceRangeSnapshot]],
) -> list[ScreenedStock]:
    result = []
    for c, price_snap in price_passed:
        corp_code = dart_client.get_corp_code(c.code)
        if corp_code is None:
            print(f"[스크리너] DART 고유번호 매칭 실패, 실적 확인 불가로 제외: {c.name}({c.code})")
            continue

        earnings = dart_client.get_latest_earnings(corp_code)
        if earnings is None:
            print(f"[스크리너] 실적 조회 실패로 제외: {c.name}({c.code})")
            continue
        if not earnings.is_improving:
            continue

        disclosures = dart_client.get_recent_disclosures(corp_code)
        news = stock_discovery.get_stock_news(c.code)
        technical = technical_indicators.analyze(c.code)
        result.append(
            ScreenedStock(
                name=c.name,
                code=c.code,
                market=c.market,
                price_range=price_snap,
                earnings=earnings,
                disclosures=disclosures,
                news=news,
                technical=technical,
            )
        )
    return result


def _stock_section_markdown(s: ScreenedStock) -> str:
    lines = [f"## {s.name} ({s.code}, {s.market})", ""]
    lines.append(
        f"- 현재가 {s.price_range.current:,.0f}원 / 52주 최고 {s.price_range.high_52w:,.0f}원 "
        f"→ 고점 대비 **-{s.price_range.pct_below_52w_high:.1f}%**"
    )
    lines.append(f"- 실적: {s.earnings.summary_line()}")
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

    lines.append("### 기술적 지표")
    t = s.technical
    if t.error:
        lines.append(f"- 조회 실패: {t.error}")
    else:
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
        for signal in t.signals:
            lines.append(f"  - 신호: {signal}")
    lines.append("")
    return "\n".join(lines)


def _summary_message(screened: list[ScreenedStock], universe_size: int, price_passed_count: int) -> str:
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"📉 저평가 스크리너 ({today} 기준)",
        f"대상 유니버스 {universe_size}종목 → 52주 고점 대비 -{DECLINE_THRESHOLD_PCT:.0f}% 이상 "
        f"{price_passed_count}종목 → 실적 개선 확인 {len(screened)}종목",
        "",
    ]
    if not screened:
        lines.append("오늘은 조건을 만족하는 종목이 없습니다.")
        return "\n".join(lines)

    for s in screened:
        lines.append(f"[{s.market}] {s.name} — 고점 대비 -{s.price_range.pct_below_52w_high:.1f}%")
        lines.append(f"  {s.earnings.summary_line()}")
        if s.technical.signals and s.technical.signals[0] != "특이 신호 없음 (평이한 구간)":
            lines.append(f"  기술: {s.technical.signals[0]}")
        lines.append("")

    lines.append("※ 상세 리포트(공시·뉴스·전체 지표) 파일 첨부")
    return "\n".join(lines)


def main() -> int:
    load_dotenv()
    log_lines = [f"[실행] {datetime.now(ZoneInfo('Asia/Seoul'))}"]

    constituents = universe.get_universe()
    log_lines.append(f"[유니버스] 코스피200+코스닥150 {len(constituents)}종목 조회")

    price_passed = _screen_by_price(constituents)
    log_lines.append(
        f"[1차:가격] 52주 고점 대비 -{DECLINE_THRESHOLD_PCT:.0f}% 이상 {len(price_passed)}종목: "
        f"{[c.name for c, _ in price_passed]}"
    )

    screened = _screen_by_earnings(price_passed)
    log_lines.append(f"[2차:실적] 실적 개선 확인 {len(screened)}종목: {[s.name for s in screened]}")

    REPORTS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    report_path = REPORTS_DIR / f"{date_str}-screener.md"
    if screened:
        content = f"# 저평가 스크리너 {date_str}\n\n" + "\n".join(_stock_section_markdown(s) for s in screened)
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
