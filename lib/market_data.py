"""지수/종목 가격 조회 및 3소스 교차검증 (한국거래소 pykrx → 네이버증권 → Yahoo Finance).

가이드 4-2 규칙:
- 채택 우선순위: pykrx → 네이버증권 → Yahoo Finance
- 값이 서로 달라도 스크립트가 임의로 제외하지 않고, 세 값을 모두 리포트에 표시한다
- 골든타임: 지수 등락률 ±3% 이상
- 외국인/기관 순매수·시가총액·거래대금은 보강 데이터. 조회 실패해도 파이프라인은 계속 진행한다
"""

import re
from dataclasses import dataclass, field
from datetime import date

import requests
import yfinance as yf
from bs4 import BeautifulSoup
from pykrx import stock

GOLDEN_TIME_THRESHOLD = 3.0

_YAHOO_INDEX_TICKER = {"코스피": "^KS11", "코스닥": "^KQ11"}
_PYKRX_INDEX_CODE = {"코스피": "1001", "코스닥": "2001"}
_NAVER_INDEX_CODE = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}

_HEADERS = {"User-Agent": "Mozilla/5.0"}


@dataclass
class PriceQuote:
    label: str
    krx: float | None = None
    naver: float | None = None
    yahoo: float | None = None
    krx_change_pct: float | None = None
    naver_change_pct: float | None = None
    yahoo_change_pct: float | None = None
    adopted: float | None = None
    adopted_source: str | None = None
    adopted_change_pct: float | None = None
    golden_time: bool = False

    def display_line(self) -> str:
        parts = []
        if self.krx is not None:
            parts.append(f"{self.krx:,.1f} (KRX)")
        if self.naver is not None:
            parts.append(f"{self.naver:,.1f} (네이버)")
        if self.yahoo is not None:
            parts.append(f"{self.yahoo:,.1f} (Yahoo)")
        adopted = f"{self.adopted:,.1f}" if self.adopted is not None else "조회 실패"
        return f"{self.label} {' / '.join(parts)} → 채택 {adopted} ({self.adopted_source})"


def _adopt(quote: PriceQuote) -> None:
    if quote.krx is not None:
        quote.adopted, quote.adopted_source, quote.adopted_change_pct = (
            quote.krx, "KRX", quote.krx_change_pct,
        )
    elif quote.naver is not None:
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
    date_str = data_date.strftime("%Y%m%d")
    result: dict[str, PriceQuote] = {}

    for label, krx_code in _PYKRX_INDEX_CODE.items():
        quote = PriceQuote(label=label)

        try:
            df = stock.get_index_ohlcv_by_date(date_str, date_str, krx_code)
            if not df.empty:
                quote.krx = float(df.iloc[-1]["종가"])
                quote.krx_change_pct = float(df.iloc[-1]["등락률"])
        except Exception:
            pass

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


_TICKER_NAME_CACHE: dict[str, str] | None = None


def _ticker_name_map() -> dict[str, str]:
    global _TICKER_NAME_CACHE
    if _TICKER_NAME_CACHE is None:
        mapping = {}
        for market in ("KOSPI", "KOSDAQ"):
            for code in stock.get_market_ticker_list(market=market):
                mapping[stock.get_market_ticker_name(code)] = code
        _TICKER_NAME_CACHE = mapping
    return _TICKER_NAME_CACHE


def resolve_ticker_by_name(stock_name: str) -> str | None:
    return _ticker_name_map().get(stock_name)


def get_stock_snapshot(stock_name: str, data_date: date) -> PriceQuote | None:
    code = resolve_ticker_by_name(stock_name)
    if code is None:
        return None

    date_str = data_date.strftime("%Y%m%d")
    quote = PriceQuote(label=stock_name)

    try:
        df = stock.get_market_ohlcv_by_date(date_str, date_str, code)
        if not df.empty:
            quote.krx = float(df.iloc[-1]["종가"])
            quote.krx_change_pct = float(df.iloc[-1]["등락률"])
    except Exception:
        pass

    try:
        naver_price, naver_pct = _fetch_naver_stock(code)
        quote.naver, quote.naver_change_pct = naver_price, naver_pct
    except Exception:
        pass

    try:
        market = "KS" if code in stock.get_market_ticker_list(market="KOSPI") else "KQ"
        yahoo_price, yahoo_pct = _fetch_yahoo_quote(f"{code}.{market}")
        quote.yahoo, quote.yahoo_change_pct = yahoo_price, yahoo_pct
    except Exception:
        pass

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


@dataclass
class SupplementaryData:
    foreign_net_buy: float | None = None
    institution_net_buy: float | None = None
    market_cap: float | None = None
    trading_value: float | None = None


def find_mentioned_ticker(text: str) -> tuple[str, str] | None:
    """text 안에 포함된 상장사명을 찾는다 (긴 이름 우선, 하드코딩 종목 리스트 없이 뉴스/주제명 기반 탐지용)."""
    names = sorted(_ticker_name_map().keys(), key=len, reverse=True)
    for name in names:
        if len(name) >= 2 and name in text:
            return name, _ticker_name_map()[name]
    return None


def get_supplementary_data(stock_name: str, data_date: date) -> SupplementaryData:
    """외국인/기관 순매수, 시가총액, 거래대금. 실패해도 None으로 채워 파이프라인을 막지 않는다."""
    supplementary = SupplementaryData()
    code = resolve_ticker_by_name(stock_name)
    if code is None:
        return supplementary

    date_str = data_date.strftime("%Y%m%d")

    try:
        trading_value_df = stock.get_market_trading_value_by_date(date_str, date_str, code)
        if not trading_value_df.empty:
            row = trading_value_df.iloc[-1]
            supplementary.foreign_net_buy = float(row.get("외국인합계", row.get("외국인", 0)))
            supplementary.institution_net_buy = float(row.get("기관합계", 0))
    except Exception:
        pass

    try:
        cap_df = stock.get_market_cap(date_str, date_str, code)
        if not cap_df.empty:
            supplementary.market_cap = float(cap_df.iloc[-1]["시가총액"])
    except Exception:
        pass

    try:
        ohlcv_df = stock.get_market_ohlcv_by_date(date_str, date_str, code)
        if not ohlcv_df.empty:
            supplementary.trading_value = float(ohlcv_df.iloc[-1]["거래대금"])
    except Exception:
        pass

    return supplementary
