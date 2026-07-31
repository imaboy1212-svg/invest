"""개별 종목 재무 밸류에이션 지표(PER·EPS·PBR·BPS·동일업종PER·배당수익률) 조회.

네이버증권 종목 페이지(finance.naver.com/item/main.naver)의 "기업현황" 탭
(PER/PBR 표)에서 가져온다. lib/market_data.py의 52주 고저 조회와 같은 페이지를
쓰지만 파싱 대상 영역이 달라 별도 모듈로 분리했다.

주의(가이드 4-4 원칙과 동일한 맥락): 이 모듈은 개발 환경 네트워크 제한으로 실제
페이지 마크업을 직접 확인하지 못한 채 작성됐다. lib/market_data.py의 52주 고저
조회가 그랬던 것처럼(모듈 docstring 참고 — "l"로 이어붙은 라벨 등 실제 마크업이
예상과 달랐음) 이 페이지도 구조가 다를 수 있다. 라벨을 하나도 못 찾으면 조용히
넘어가지 않고 원본 텍스트 일부를 로그로 남기니, GitHub Actions workflow_dispatch로
실행한 뒤 로그를 보고 `_num_after`의 라벨 문자열/탐색 범위를 실제 구조에 맞게
조정할 것.
"""

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0"}


@dataclass
class Fundamentals:
    code: str
    per: float | None = None
    eps: float | None = None
    pbr: float | None = None
    bps: float | None = None
    industry_per: float | None = None
    dividend_yield_pct: float | None = None


def _num_after(text: str, label: str, window: int = 30) -> float | None:
    """label이 처음 등장하는 위치 바로 뒤 window자 안에서 첫 숫자를 찾는다."""
    idx = text.find(label)
    if idx == -1:
        return None
    match = re.search(r"([+-]?[\d,]+\.?\d*)", text[idx + len(label): idx + len(label) + window])
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def get_fundamentals(code: str) -> Fundamentals | None:
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        print(f"[재무지표] 페이지 조회 실패(code={code}): {type(exc).__name__}: {exc}")
        return None

    # "기업현황" 탭(#tab_con1) 안의 PER/PBR 표(table.per_table)로 범위를 좁혀서
    # 페이지 다른 곳의 "PER" 언급(관련 종목 비교 링크 등)과 헷갈리지 않게 한다.
    # 둘 다 없으면 페이지 전체 텍스트로 폴백(정확도는 떨어지지만 완전 실패보다 낫다).
    section = soup.select_one("#tab_con1") or soup.select_one("table.per_table")
    if section is None:
        print(f"[재무지표] PER/PBR 표 셀렉터 매칭 실패(code={code}) — 페이지 전체 텍스트로 폴백")
    text = section.get_text(" ", strip=True) if section is not None else soup.get_text(" ", strip=True)

    result = Fundamentals(
        code=code,
        per=_num_after(text, "PER"),
        eps=_num_after(text, "EPS"),
        pbr=_num_after(text, "PBR"),
        bps=_num_after(text, "BPS"),
        industry_per=_num_after(text, "동일업종"),
        dividend_yield_pct=_num_after(text, "배당수익률"),
    )

    if result.per is None and result.eps is None and result.pbr is None and result.bps is None:
        print(f"[재무지표] 전체 조회 실패(code={code}) — PER/EPS/PBR/BPS 라벨을 하나도 못 찾음: {text[:300]!r}")
        return None

    return result
