"""종목명/종목코드 입력 → (정식 종목명, 종목코드) 해석 — stock_analyzer.py 전용.

6자리 숫자면 종목코드로 바로 취급한다. 그 외 문자열(종목명)은:
1. 코스피200+코스닥150 유니버스(lib/universe.py, 이미 이 환경에서 동작이 검증된
   entryJongmok.naver/sise_market_sum.naver 소스)에서 이름이 정확히 일치하거나
   포함되는 종목을 우선 채택한다 — 대부분의 분석 대상은 이 유니버스 안에 있다.
2. 유니버스에 없으면 네이버증권 모바일 자동완성 API(m.stock.naver.com)를
   최후 수단으로 시도한다. lib/stock_discovery.py가 같은 m.stock.naver.com
   도메인의 다른 API(뉴스 목록)를 이미 성공적으로 쓰고 있어 도메인 자체는
   검증됐지만, 이 특정 엔드포인트(자동완성)는 실전 로그로 아직 확인 못 했다 —
   실패해도 조용히 넘어가고 로그만 남긴다.

(2026-07-31 실전 로그로 확인: finance.naver.com/search/searchList.naver는
404 — 더 이상 존재하지 않는 URL이라 제거함. 검색은 위 두 단계로 대체.)

정식 종목명은 종목 상세 페이지의 <title> 태그("삼성전자 : 네이버 증권")에서
가져온다 — title 태그는 본문 마크업보다 바뀔 가능성이 낮다는 판단.
"""

import re

import requests
from bs4 import BeautifulSoup

from lib import universe

_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _fetch_name(code: str) -> str | None:
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        print(f"[종목검색] 종목명 조회 실패(code={code}): {type(exc).__name__}: {exc}")
        return None

    if soup.title is None:
        return None
    name = soup.title.get_text(strip=True).split(":")[0].strip()
    return name or None


def _search_universe(query: str) -> tuple[str, str] | None:
    constituents = universe.get_universe()
    if not constituents:
        print("[종목검색] 유니버스 조회 결과 0건 — 유니버스 기반 검색 건너뜀")
        return None

    for c in constituents:
        if c.name == query:
            return c.name, c.code
    for c in constituents:
        if query in c.name:
            return c.name, c.code
    return None


def _search_autocomplete(query: str) -> tuple[str, str] | None:
    """코스피200/코스닥150 밖의 종목(소형주 등)을 위한 최후 수단. 실패해도 예외를
    올리지 않고 None을 반환 — resolve()가 "종목을 찾을 수 없음"으로 명확히 보고한다."""
    try:
        resp = requests.get(
            "https://m.stock.naver.com/api/search/autoComplete",
            headers=_HEADERS,
            params={"query": query},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"[종목검색] 자동완성 API 실패({query!r}): {type(exc).__name__}: {exc}")
        return None

    items = data.get("result", {}).get("items", []) if isinstance(data, dict) else []
    for item in items:
        code = item.get("code") or item.get("itemCode") or item.get("cd")
        name = item.get("name") or item.get("stockName")
        if code and name and re.fullmatch(r"\d{6}", code):
            return name, code

    print(f"[종목검색] 자동완성 API 결과 없음/형식 불일치({query!r}): {str(data)[:200]!r}")
    return None


def resolve(query: str) -> tuple[str, str] | None:
    query = query.strip()
    if re.fullmatch(r"\d{6}", query):
        name = _fetch_name(query)
        return (name or query), query

    found = _search_universe(query)
    if found:
        return found
    return _search_autocomplete(query)
