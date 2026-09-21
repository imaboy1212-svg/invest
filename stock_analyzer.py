"""종목분석기 — 종목명 또는 종목코드를 입력하면 객관적 재무 데이터(EPS·BPS·
동일업종 PER)로 적정주가를 산출해 현재가와 비교한다.

사용법:
    python3 stock_analyzer.py 삼성전자
    python3 stock_analyzer.py 005930 --telegram

밸류에이션(lib/valuation.py)은 성장률 전망 같은 주관적 가정이 필요 없는 2가지
방법만 쓴다 — PER 밸류에이션(EPS × 동일업종 PER), 그레이엄 공식
(sqrt(22.5 × EPS × BPS)). 두 방법의 평균을 적정주가 추정치로 삼아 현재가와의
괴리율로 저평가/고평가/적정 구간을 판정한다. 필요한 데이터가 없으면 해당
방법은 건너뛰고, 둘 다 없으면 "판단 불가"로 명시한다 — 근거 없이 숫자를
만들어내지 않는다(가이드 4-4 원칙).

기술적 지표·직전 분기 실적·공시·뉴스(기존 lib 재사용)는 참고정보로만 보여주고
밸류에이션 판정에는 반영하지 않는다 — 가격 추세와 적정가격은 별개 질문이라는
stock_screener.py의 기존 설계와 같은 원칙이다.

실제 데이터 수집·조합(종목 해석 → 시세 → 재무지표 → 밸류에이션)은
lib/analysis.py에 있다 — bestwellth.org 위젯용 api_server.py와 이 파이프라인을
공유한다.

리포트는 analysis_reports/에 마크다운으로 저장하고 콘솔에도 출력한다.
--telegram을 주면 텔레그램으로도 요약+리포트 파일을 전송한다(기본은 저장만).
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from lib import analysis, telegram_client
from lib.analysis import AnalysisResult, PriceFetchError, StockNotFoundError

REPORTS_DIR = Path(__file__).resolve().parent / "analysis_reports"


def _fmt(value: float | None, unit: str = "") -> str:
    return f"{value:,.2f}{unit}" if value is not None else "조회 실패"


def _build_report(result: AnalysisResult) -> str:
    price_range = result.price_range
    fund = result.fundamentals
    val_result = result.valuation
    technical = result.technical

    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
    lines = [f"# 종목분석 — {result.name} ({result.code})", f"_{today} 기준_", ""]

    lines.append("## 적정주가 판단")
    lines.append(f"- 현재가: {price_range.current:,.0f}원")
    if val_result.estimates:
        for e in val_result.estimates:
            lines.append(f"- {e.method}: **{e.fair_price:,.0f}원** ({e.basis})")
        lines.append(f"- 적정주가 추정 평균: **{val_result.average_fair_price:,.0f}원**")
        lines.append(f"- 현재가 대비 괴리율: **{val_result.gap_pct:+.1f}%**")
    else:
        lines.append("- 산출 가능한 밸류에이션 방법 없음 (EPS·BPS·동일업종 PER 데이터 부족)")
    lines.append(f"- **판정: {val_result.verdict}**")
    lines.append("")

    lines.append("## 원본 재무 데이터 (네이버증권)")
    if fund is not None:
        lines.append(f"- PER {_fmt(fund.per, '배')} / EPS {_fmt(fund.eps, '원')}")
        lines.append(f"- PBR {_fmt(fund.pbr, '배')} / BPS {_fmt(fund.bps, '원')}")
        lines.append(f"- 동일업종 PER {_fmt(fund.industry_per, '배')}")
        lines.append(f"- 배당수익률 {_fmt(fund.dividend_yield_pct, '%')}")
    else:
        lines.append("- 조회 실패")
    lines.append("")

    lines.append("## 52주 가격 위치")
    lines.append(f"- 52주 최고 {price_range.high_52w:,.0f}원 / 최저 {price_range.low_52w:,.0f}원")
    lines.append(f"- 현재가는 52주 고점 대비 -{price_range.pct_below_52w_high:.1f}%")
    lines.append("")

    lines.append("## 참고: 기술적 지표 (밸류에이션 판정에는 미반영)")
    if technical.error:
        lines.append(f"- 조회 실패: {technical.error}")
    else:
        for s in technical.signal_lines():
            lines.append(f"- {s}")
    lines.append("")

    lines.append("## 참고: 직전 분기 실적 (DART)")
    lines.append(f"- {result.earnings.summary_line() if result.earnings else '조회 실패'}")
    lines.append("")

    lines.append("## 참고: 최근 공시")
    if result.disclosures:
        for d in result.disclosures:
            lines.append(f"- [{d.receipt_date}] {d.report_name} ({d.url})")
    else:
        lines.append("- 최근 14일 내 공시 없음(또는 조회 실패)")
    lines.append("")

    lines.append("## 참고: 최근 뉴스")
    if result.news:
        for n in result.news:
            lines.append(f"- {n}")
    else:
        lines.append("- 조회된 뉴스 없음")
    lines.append("")

    return "\n".join(lines)


def _telegram_summary(result: AnalysisResult) -> str:
    val_result = result.valuation
    lines = [f"📊 {result.name}({result.code}) 종목분석", f"현재가 {result.price_range.current:,.0f}원"]
    if val_result.average_fair_price is not None:
        lines.append(f"적정주가 추정 {val_result.average_fair_price:,.0f}원 (괴리율 {val_result.gap_pct:+.1f}%)")
    lines.append(f"판정: {val_result.verdict}")
    lines.append("※ 상세 리포트 파일 첨부")
    return "\n".join(lines)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="종목분석기 — 객관적 데이터 기반 적정주가 판단")
    parser.add_argument("query", help="종목명 또는 6자리 종목코드 (예: 삼성전자, 005930)")
    parser.add_argument("--telegram", action="store_true", help="결과를 텔레그램으로도 전송")
    args = parser.parse_args()

    try:
        result = analysis.run_analysis(args.query, include_reference_info=True)
    except StockNotFoundError as exc:
        print(f"[종목분석기] {exc}")
        return 1
    except PriceFetchError as exc:
        print(f"[종목분석기] {exc} — 분석 중단")
        return 1

    print(f"[종목분석기] 분석 대상: {result.name} ({result.code})")

    report = _build_report(result)
    print("\n" + report)

    REPORTS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    report_path = REPORTS_DIR / f"{date_str}-{result.name}-분석.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"[종목분석기] 리포트 저장: {report_path}")

    if args.telegram:
        telegram_client.send_message(_telegram_summary(result))
        telegram_client.send_document(report_path)
        print("[종목분석기] 텔레그램 전송 완료")

    return 0


if __name__ == "__main__":
    sys.exit(main())
