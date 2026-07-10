"""종목리포트팀용 후보 종목 발굴.

하드코딩 리스트 없이, 네이버증권 코스피·코스닥 인기종목 중 실제 뉴스/이슈가 있는
종목만 후보로 남긴다. 뉴스가 없는 인기종목은 억지로 주제화하지 않고 후보에서 뺀다
(가이드 4-4 원칙: 확인 안 되면 만들지 않는다).

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


@dataclass
class StockCandidate:
    name: str
    code: str
    reasons: list[str] = field(default_factory=list)
    has_news: bool = True  # 이 함수가 반환하는 후보는 전부 뉴스가 확인된 것만이다

    def summary_line(self) -> str:
        return f"{self.name} (네이버 인기종목, 뉴스 있음)"


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


def discover_candidates(data_date: date, news_headlines: list[str]) -> list[StockCandidate]:
    """네이버 인기종목 중 뉴스에 이름이 언급된 종목만 순위 그대로 반환한다."""
    combined_headlines = " ".join(news_headlines)

    candidates = []
    for name, code in _naver_popular_stocks().items():
        if name in combined_headlines:
            candidates.append(StockCandidate(name=name, code=code))

    return candidates
