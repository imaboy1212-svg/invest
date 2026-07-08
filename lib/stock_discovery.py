"""종목리포트팀용 후보 종목 발굴.

하드코딩 리스트 없이, 매일 아래 기준을 종합 판단해 후보를 뽑는다 (단일 기준 아님):
1) 거래대금 급증 (pykrx 거래대금 상위)
2) 네이버증권 실시간 인기검색 종목
3) 장마감 등락률 상위 — 급등 + 급락 둘 다 포함 (pykrx 등락률 절대값 상위)

세 기준 중 여러 개에 걸치거나 관련 뉴스가 있는 종목을 우선 선정한다.
개별 소스 실패는 건너뛰고 계속 진행한다 (전체 파이프라인을 막지 않음).
"""

from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import requests
from bs4 import BeautifulSoup
from pykrx import stock

_HEADERS = {"User-Agent": "Mozilla/5.0"}
TOP_N = 15
NEWS_BONUS = 2


@dataclass
class StockCandidate:
    name: str
    code: str
    reasons: list[str] = field(default_factory=list)
    has_news: bool = False

    @property
    def score(self) -> int:
        return len(self.reasons) + (NEWS_BONUS if self.has_news else 0)

    def summary_line(self) -> str:
        tags = list(self.reasons)
        if self.has_news:
            tags.append("뉴스 있음")
        return f"{self.name} ({', '.join(tags)})"


def _market_ohlcv_all(data_date: date) -> pd.DataFrame:
    """KOSPI+KOSDAQ 전체 종목의 당일 OHLCV(거래대금/등락률 포함)를 합쳐서 반환. 실패 시 빈 DataFrame."""
    date_str = data_date.strftime("%Y%m%d")
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = stock.get_market_ohlcv_by_ticker(date_str, market=market)
            if not df.empty:
                frames.append(df)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames)


def _top_by_trading_value(df) -> dict[str, str]:
    if df.empty or "거래대금" not in df.columns:
        return {}
    top = df.sort_values("거래대금", ascending=False).head(TOP_N)
    return {stock.get_market_ticker_name(code): code for code in top.index}


def _top_gainers(df) -> dict[str, str]:
    if df.empty or "등락률" not in df.columns:
        return {}
    top = df.sort_values("등락률", ascending=False).head(TOP_N)
    return {stock.get_market_ticker_name(code): code for code in top.index}


def _top_losers(df) -> dict[str, str]:
    if df.empty or "등락률" not in df.columns:
        return {}
    top = df.sort_values("등락률", ascending=True).head(TOP_N)
    return {stock.get_market_ticker_name(code): code for code in top.index}


def _naver_realtime_hot_stocks() -> dict[str, str]:
    """네이버증권 실시간 인기검색 종목. 장 운영시간 외/주말엔 비어있을 수 있다."""
    try:
        resp = requests.get(
            "https://finance.naver.com/sise/lastsearch2.naver", headers=_HEADERS, timeout=10
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        return {}

    result = {}
    for a in soup.select("a.tltle"):
        href = a.get("href", "")
        name = a.get_text(strip=True)
        if "code=" in href and name:
            code = href.split("code=")[-1]
            result[name] = code
    return result


def discover_candidates(data_date: date, news_headlines: list[str]) -> list[StockCandidate]:
    candidates: dict[str, StockCandidate] = {}

    def _add(name_to_code: dict[str, str], reason: str) -> None:
        for name, code in name_to_code.items():
            if name not in candidates:
                candidates[name] = StockCandidate(name=name, code=code)
            candidates[name].reasons.append(reason)

    ohlcv_all = _market_ohlcv_all(data_date)
    _add(_top_by_trading_value(ohlcv_all), "거래대금 급증")
    _add(_top_gainers(ohlcv_all), "급등(상승률 상위)")
    _add(_top_losers(ohlcv_all), "급락(하락률 상위)")
    _add(_naver_realtime_hot_stocks(), "네이버 실시간 인기검색")

    combined_headlines = " ".join(news_headlines)
    for candidate in candidates.values():
        if candidate.name in combined_headlines:
            candidate.has_news = True

    return sorted(candidates.values(), key=lambda c: c.score, reverse=True)
