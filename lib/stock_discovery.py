"""종목리포트팀용 후보 종목 발굴.

하드코딩 리스트 없이, 네이버증권 코스피·코스닥 인기종목 중 실제 뉴스·공시가 있는
종목만 후보로 남긴다. 뉴스가 없는 인기종목은 억지로 주제화하지 않고 후보에서 뺀다
(가이드 4-4 원칙: 확인 안 되면 만들지 않는다).

각 인기종목마다 네이버증권 개별 종목 뉴스 목록(공시 포함)을 직접 조회해서, 그
종목에 실제로 최근 뉴스·공시가 있는지 확인하고 해당 헤드라인을 근거 텍스트로
함께 제공한다. 전체 증권 뉴스 헤드라인에 종목명이 우연히 언급되길 기다리는
방식보다 훨씬 직접적이고 안정적이다.

이전에는 pykrx 거래대금/등락률 상위를 추가 기준으로 같이 썼으나, 이 실행 환경에서
KRX가 pykrx의 직접 조회를 계속 차단해 해당 기준이 실제로는 단 한 번도 후보를
채우지 못했다 (매 실행 로그에서 항상 빈 결과). 조건을 여러 개 겹쳐서 오히려 발굴이
까다로워지는 문제가 있어, 실제로 동작하는 네이버 인기종목 하나로 단순화했다.

완료 주제(같은 종목 재추천 금지)는 completed_topics.json의 이름 매칭으로
후속 단계(completed_topics.filter_new_topics)에서 걸러진다.
"""

from dataclasses import dataclass, field
from datetime import date

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0"}
NEWS_PER_STOCK = 5


@dataclass
class StockCandidate:
    name: str
    code: str
    news_headlines: list[str] = field(default_factory=list)

    @property
    def has_news(self) -> bool:
        return bool(self.news_headlines)

    def summary_line(self) -> str:
        return f"{self.name} (네이버 인기종목, 관련 뉴스·공시 {len(self.news_headlines)}건)"


def _naver_popular_stocks() -> dict[str, str]:
    """네이버증권 코스피·코스닥 인기종목(실시간 인기검색). 장 운영시간 외/주말엔 비어있을 수 있다."""
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


def _naver_stock_news(code: str) -> list[str]:
    """네이버증권 개별 종목 뉴스·공시 목록. 실패/없음이면 빈 리스트.

    finance.naver.com/item/news_news.naver 는 실제로는 AI 뉴스클러스터링 위젯을
    서버 렌더링만 해두고(빈 검색어로 '뉴스 없음' 문구만 나옴) 실데이터는 JS로
    다시 불러오는 페이지라 정적 파싱이 불가능했다. 대신 네이버 모바일증권이
    쓰는 JSON API(m.stock.naver.com)를 직접 호출한다.
    """
    try:
        resp = requests.get(
            f"https://m.stock.naver.com/api/news/stock/{code}",
            headers=_HEADERS,
            params={"pageSize": NEWS_PER_STOCK, "page": 1},
            timeout=10,
        )
        resp.raise_for_status()
        raw_text = resp.text
        data = resp.json()
    except Exception as exc:
        print(f"[진단] 종목뉴스 요청/파싱 예외 (code={code}): {exc}")
        return []

    # 응답 형태는 리스트 그대로거나 {"items": [...]}/{"list": [...]} 형태일 수 있음
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("items") or data.get("list") or []
        # 그룹핑된 형태({"itemList":[{"items":[...]}]}) 대응
        if not items and "itemList" in data:
            items = [i for group in data["itemList"] for i in group.get("items", [])]
    else:
        items = []

    headlines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("subtitle") or ""
        title = BeautifulSoup(title, "lxml").get_text(strip=True) if title else ""
        if not title:
            continue
        source = item.get("officeName") or item.get("office") or "네이버증권"
        headlines.append(f"[{source}] {title}")
        if len(headlines) >= NEWS_PER_STOCK:
            break

    if not headlines:
        print(f"[진단] 종목뉴스 0건 (code={code}): {raw_text[:200]}")
    return headlines


def discover_candidates(data_date: date) -> list[StockCandidate]:
    """네이버 인기종목 중 개별 종목 뉴스·공시가 실제로 있는 종목만 반환한다."""
    candidates = []
    for name, code in _naver_popular_stocks().items():
        headlines = _naver_stock_news(code)
        if headlines:
            candidates.append(StockCandidate(name=name, code=code, news_headlines=headlines))

    return candidates
