"""종목분석 파이프라인 — stock_analyzer.py(CLI)와 api_server.py(웹 위젯 API)가
공유한다. 종목 해석 → 현재가/52주 조회 → 재무지표 조회 → 적정주가 산출 →
(선택) 기술적 지표·실적·공시·뉴스 참고정보 수집까지 한 번에 묶는다.

CLI와 API가 각자 markdown/JSON으로 다르게 표현하므로 그 부분은 여기 넣지 않고,
두 쪽 모두 필요한 원자료 수집·조합만 여기서 한다.
"""

from dataclasses import dataclass, field

from lib import dart_client, fundamentals, market_data, stock_discovery, stock_search, technical_indicators, valuation


class StockNotFoundError(Exception):
    def __init__(self, query: str):
        super().__init__(f"종목을 찾을 수 없음: {query!r}")
        self.query = query


class PriceFetchError(Exception):
    def __init__(self, code: str):
        super().__init__(f"현재가 조회 실패: code={code}")
        self.code = code


@dataclass
class AnalysisResult:
    name: str
    code: str
    price_range: market_data.PriceRangeSnapshot
    fundamentals: fundamentals.Fundamentals | None
    valuation: valuation.ValuationResult
    technical: technical_indicators.TechnicalSnapshot
    earnings: dart_client.EarningsSnapshot | None = None
    disclosures: list[dart_client.Disclosure] = field(default_factory=list)
    news: list[str] = field(default_factory=list)


def run_analysis(query: str, include_reference_info: bool = True) -> AnalysisResult:
    """include_reference_info=False면 DART 실적/공시·뉴스 조회를 건너뛴다 —
    웹 위젯처럼 응답 속도가 중요한 호출부용(적정주가 산출 자체에는 영향 없음,
    그 정보는 어차피 참고용이라 밸류에이션 판정에 반영되지 않는다)."""
    resolved = stock_search.resolve(query)
    if resolved is None:
        raise StockNotFoundError(query)
    name, code = resolved

    price_range = market_data.get_price_and_52w_range(code)
    if price_range is None:
        raise PriceFetchError(code)

    fund = fundamentals.get_fundamentals(code)
    val_result = valuation.evaluate(
        current_price=price_range.current,
        eps=fund.eps if fund else None,
        bps=fund.bps if fund else None,
        industry_per=fund.industry_per if fund else None,
    )
    technical = technical_indicators.analyze(code)

    earnings, disclosures, news = None, [], []
    if include_reference_info:
        try:
            corp_code = dart_client.get_corp_code(code)
            earnings = dart_client.get_latest_earnings(corp_code) if corp_code else None
            disclosures = dart_client.get_recent_disclosures(corp_code) if corp_code else []
        except KeyError:
            pass
        news = stock_discovery.get_stock_news(code)

    return AnalysisResult(
        name=name,
        code=code,
        price_range=price_range,
        fundamentals=fund,
        valuation=val_result,
        technical=technical,
        earnings=earnings,
        disclosures=disclosures,
        news=news,
    )
