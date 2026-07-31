"""종목명/종목코드 입력 → (정식 종목명, 종목코드) 해석 — stock_analyzer.py 전용.

6자리 숫자면 종목코드로 바로 취급한다. 그 외 문자열은 네이버증권 통합검색 결과
페이지에서 "code=" 링크가 붙은 첫 번째 종목을 채택한다 — lib/universe.py,
lib/stock_discovery.py가 이미 쓰고 있는 "a[href*=code=]" 패턴과 동일한 방식이라
페이지 마크업(클래스명 등)이 바뀌어도 비교적 안전하다.

정식 종목명은 종목 상세 페이지의 <title> 태그("삼성전자 : 네이버 증권")에서
가져온다 — title 태그는 본문 마크업보다 바뀔 가능성이 낮다는 판단.
"""

import re

import requests
from bs4 import BeautifulSoup

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


def _search_by_name(query: str) -> tuple[str, str] | None:
    try:
        resp = requests.get(
            "https://finance.naver.com/search/searchList.naver",
            headers=_HEADERS,
            params={"query": query},
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        print(f"[종목검색] 검색 실패({query!r}): {type(exc).__name__}: {exc}")
        return None

    for a in soup.select("a"):
        href = a.get("href", "")
        code_match = re.search(r"code=(\d{6})", href)
        if not code_match:
            continue
        name = a.get_text(strip=True)
        if name:
            return name, code_match.group(1)

    print(f"[종목검색] 검색 결과 없음({query!r}) — 페이지 구조 변경 가능성")
    return None


def resolve(query: str) -> tuple[str, str] | None:
    query = query.strip()
    if re.fullmatch(r"\d{6}", query):
        name = _fetch_name(query)
        return (name or query), query
    return _search_by_name(query)
