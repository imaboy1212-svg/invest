"""지수/종목 가격 조회 및 교차검증 (네이버증권 → Yahoo Finance).

가이드 4-2 규칙:
- 값이 서로 달라도 스크립트가 임의로 제외하지 않고, 두 값을 모두 리포트에 표시한다
- 골든타임: 지수 등락률 ±3% 이상

KRX(pykrx) 직접조회 제거 안내 (2026-07-20):
이 실행 환경(GitHub Actions)에서 pykrx의 KRX 직접호출은 수십 회 실행 동안 단 한 번도
성공한 적이 없다 (매번 "Expecting value: line 1 column 1" 빈 응답 실패 → KRX가 클라우드
IP를 차단하는 것으로 추정). "KRX 로그인 실패" 메시지는 pykrx 자체의 정보성 출력일 뿐
원인이 아니며(로그인 없이도 동작하는 라이브러리), 실계정 로그인을 등록해도 IP 차단이면
해결되지 않을 가능성이 높아 코로 님과 상의 후 pykrx 직접호출을 전부 제거하기로 결정함.
지수/종목 가격은 네이버증권 → Yahoo Finance 2소스 교차검증으로 대체.
"""

import re
from dataclasses import dataclass
from datetime import date

import requests
import yfinance as yf
from bs4 import BeautifulSoup

GOLDEN_TIME_THRESHOLD = 3.0

_YAHOO_INDEX_TICKER = {"코스피": "^KS11", "코스닥": "^KQ11"}
_NAVER_INDEX_CODE = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}

_HEADERS = {"User-Agent": "Mozilla/5.0"}


@dataclass
class PriceQuote:
    label: str
    naver: float | None = None
    yahoo: float | None = None
    naver_change_pct: float | None = None
    yahoo_change_pct: float | None = None
    adopted: float | None = None
    adopted_source: str | None = None
    adopted_change_pct: float | None = None
    golden_time: bool = False

    def display_line(self) -> str:
        parts = []
        if self.naver is not None:
            parts.append(f"{self.naver:,.1f} (네이버)")
        if self.yahoo is not None:
            parts.append(f"{self.yahoo:,.1f} (Yahoo)")
        adopted = f"{self.adopted:,.1f}" if self.adopted is not None else "조회 실패"
        return f"{self.label} {' / '.join(parts)} → 채택 {adopted} ({self.adopted_source})"


def _adopt(quote: PriceQuote) -> None:
    if quote.naver is not None:
        quote.adopted, quote.adopted_source, quote.adopted_change_pct = (
            quote.naver, "네이버", quote.naver_change_pct,
        )
    elif quote.yahoo is not None:
        quote.adopted, quote.adopted_source, quote.adopted_change_pct = (
            quote.yahoo, "Yahoo", quote.yahoo_change_pct,
        )
    if quote.adopted_change_pct is not None:
        quote.golden_time = abs(quote.adopted_change_pct) >= GOLDEN_TIME_THRESHOLD


def _pct_change(prev: float, curr: float) -> float:
    return (curr - prev) / prev * 100


def get_index_snapshot(data_date: date) -> dict[str, PriceQuote]:
    result: dict[str, PriceQuote] = {}

    for label in _NAVER_INDEX_CODE:
        quote = PriceQuote(label=label)

        try:
            naver_price, naver_pct = _fetch_naver_index(_NAVER_INDEX_CODE[label])
            quote.naver, quote.naver_change_pct = naver_price, naver_pct
        except Exception:
            pass

        try:
            yahoo_price, yahoo_pct = _fetch_yahoo_quote(_YAHOO_INDEX_TICKER[label])
            quote.yahoo, quote.yahoo_change_pct = yahoo_price, yahoo_pct
        except Exception:
            pass

        _adopt(quote)
        result[label] = quote

    return result


_GLOBAL_YAHOO_TICKERS = {
    "S&P500": "^GSPC",
    "나스닥": "^IXIC",
    "다우존스": "^DJI",
    "원/달러 환율": "KRW=X",
    "WTI 유가": "CL=F",
}


def get_global_market_lines() -> list[str]:
    """마켓칼럼용 해외 지수·환율·유가 스냅샷. Yahoo Finance 단일 소스, 실패한 항목은 건너뛴다."""
    lines = []
    for label, ticker in _GLOBAL_YAHOO_TICKERS.items():
        try:
            price, pct = _fetch_yahoo_quote(ticker)
        except Exception:
            continue
        lines.append(f"{label} {price:,.2f} ({pct:+.2f}%) (출처: Yahoo)")
    return lines


def _fetch_naver_index(naver_code: str) -> tuple[float, float]:
    url = f"https://finance.naver.com/sise/sise_index.naver?code={naver_code}"
    resp = requests.get(url, headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    price_text = soup.select_one("#now_value").get_text(strip=True)
    change_text = soup.select_one("#change_value_and_rate").get_text(" ", strip=True)
    price = float(price_text.replace(",", ""))
    match = re.search(r"([+-]?\d+\.\d+)\s*%", change_text)
    pct = float(match.group(1)) if match else 0.0
    if "하락" in change_text and pct > 0:
        pct = -pct
    return price, pct


def _fetch_yahoo_quote(ticker: str) -> tuple[float, float]:
    t = yf.Ticker(ticker)
    hist = t.history(period="5d")
    if hist.empty:
        raise ValueError(f"yahoo history empty for {ticker}")
    curr = float(hist["Close"].iloc[-1])
    prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else curr
    return curr, _pct_change(prev, curr)


def get_stock_snapshot(stock_name: str, data_date: date, code: str) -> PriceQuote | None:
    """code는 호출부(stock_discovery가 네이버에서 이미 확보한 종목코드)에서 반드시 넘겨야 한다."""
    if code is None:
        return None

    quote = PriceQuote(label=stock_name)

    try:
        naver_price, naver_pct = _fetch_naver_stock(code)
        quote.naver, quote.naver_change_pct = naver_price, naver_pct
    except Exception:
        pass

    for suffix in ("KS", "KQ"):
        try:
            yahoo_price, yahoo_pct = _fetch_yahoo_quote(f"{code}.{suffix}")
            quote.yahoo, quote.yahoo_change_pct = yahoo_price, yahoo_pct
            break
        except Exception:
            continue

    _adopt(quote)
    return quote


def _fetch_naver_stock(code: str) -> tuple[float, float]:
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    resp = requests.get(url, headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    price_text = soup.select_one(".no_today .blind").get_text(strip=True)
    change_area = soup.select_one(".no_exday")
    exday_blinds = change_area.select(".blind") if change_area else []
    price = float(price_text.replace(",", ""))
    pct = 0.0
    if len(exday_blinds) >= 2:
        pct = float(exday_blinds[1].get_text(strip=True).replace(",", ""))
        if change_area and "하락" in change_area.get_text():
            pct = -pct
    return price, pct
