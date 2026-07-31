"""개별 종목 재무 밸류에이션 지표(PER·EPS·PBR·BPS·동일업종PER·배당수익률) 조회.

네이버증권 종목 페이지(finance.naver.com/item/main.naver)의 "기업현황" 탭
(PER/PBR 표, table.per_table)에서 가져온다. lib/market_data.py의 52주 고저
조회와 같은 페이지를 쓰지만 파싱 대상 영역이 달라 별도 모듈로 분리했다.

2026-07-31 실전 로그로 확인한 실제 표기 형태(scratch_diagnose_fundamentals.py):
table.per_table 안에 "PER l EPS (2026.03) PER = 현재가 ÷ EPS ... 계산합니다.
20.61 배 l 12,372 원 추정PER l EPS ... 5.00 배 l 46,635 원 PBR l BPS (2026.03)
... 3.55 배 l 71,907 원 배당수익률 l 2025.12 ... 0.65 %" 순서로, 각 라벨 뒤에
계산식 설명 문단이 먼저 오고 그 다음에 실제 값이 "숫자 배 l 숫자 원" 형태로
나온다. 라벨 바로 뒤 숫자를 찾는 방식(초기 버전)은 설명 문단 안의 기준연월
"(2026.03)"을 값으로 잘못 집어오는 버그가 있었다 — 그래서 값 자체의 형태
("N 배 l N 원")로 직접 찾는다. 동일업종 PER은 per_table 밖(#tab_con1 안)에
별도로 있다.

값 쌍은 이 순서로 3개 등장한다: [0]=PER/EPS, [1]=추정PER/추정EPS(미사용),
[2]=PBR/BPS. 순서가 바뀌면(페이지 개편) 잘못된 값을 짝지을 수 있으니, 라벨이
바뀌면 이 가정도 다시 확인해야 한다.
"""

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0"}

_PAIR_PATTERN = re.compile(r"([\d,]+\.?\d*)\s*배\s*l\s*([\d,]+\.?\d*)\s*원")
_PCT_PATTERN = re.compile(r"([\d,]+\.?\d*)\s*%")
_INDUSTRY_PER_PATTERN = re.compile(r"동일업종\s*PER\D*?([\d,]+\.?\d*)\s*배")


def _to_float(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


@dataclass
class Fundamentals:
    code: str
    per: float | None = None
    eps: float | None = None
    pbr: float | None = None
    bps: float | None = None
    industry_per: float | None = None
    dividend_yield_pct: float | None = None


def get_fundamentals(code: str) -> Fundamentals | None:
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        print(f"[재무지표] 페이지 조회 실패(code={code}): {type(exc).__name__}: {exc}")
        return None

    per_table = soup.select_one("table.per_table")
    if per_table is None:
        print(f"[재무지표] table.per_table 셀렉터 매칭 실패(code={code}) — 페이지 구조 변경 가능성")
        return None

    pt_text = per_table.get_text(" ", strip=True)
    pairs = _PAIR_PATTERN.findall(pt_text)

    per = eps = pbr = bps = None
    if len(pairs) >= 1:
        per, eps = _to_float(pairs[0][0]), _to_float(pairs[0][1])
    if len(pairs) >= 3:
        pbr, bps = _to_float(pairs[2][0]), _to_float(pairs[2][1])

    dividend_match = _PCT_PATTERN.search(pt_text)
    dividend_yield_pct = _to_float(dividend_match.group(1)) if dividend_match else None

    industry_per = None
    tab_con1 = soup.select_one("#tab_con1")
    if tab_con1 is not None:
        industry_match = _INDUSTRY_PER_PATTERN.search(tab_con1.get_text(" ", strip=True))
        industry_per = _to_float(industry_match.group(1)) if industry_match else None

    if per is None and eps is None and pbr is None and bps is None:
        print(f"[재무지표] 전체 조회 실패(code={code}) — 값 쌍('N 배 l N 원') 패턴을 하나도 못 찾음: {pt_text[:300]!r}")
        return None

    return Fundamentals(
        code=code,
        per=per,
        eps=eps,
        pbr=pbr,
        bps=bps,
        industry_per=industry_per,
        dividend_yield_pct=dividend_yield_pct,
    )
