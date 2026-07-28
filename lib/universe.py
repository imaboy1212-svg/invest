"""코스피200·코스닥150 구성종목 유니버스 조회.

코스피200은 네이버증권 entryJongmok.naver(type=KPI200)가 안정적으로 동작하는
것으로 확인됐다 (기존 lib/stock_discovery.py 등에서 같은 도메인 크롤링 검증됨).

코스닥150은 네이버증권에 동일한 목적의 페이지(type=KOSDAQ150로 추정)가 있지만,
이 저장소를 만든 시점에는 실행 환경의 네트워크 정책상 실제 응답을 직접 확인하지
못했다 (프록시가 finance.naver.com으로의 외부 요청을 차단). 그래서:
1) 우선 네이버 조회를 시도하고,
2) 실패하거나 0건이면 로컬 백업 파일(kosdaq150_constituents.json)로 폴백한다.

폴백 파일은 코스닥150이 반기(6월/12월)마다 정기 변경되므로, 실패 로그가 찍히면
KRX 지수정보시스템이나 KODEX 코스닥150 ETF PDF(자산구성내역)를 참고해 사람이
수동으로 채워 넣어야 한다. 첫 실행 후 로그를 반드시 확인할 것.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_KOSDAQ150_FALLBACK_PATH = Path(__file__).resolve().parent.parent / "kosdaq150_constituents.json"
_MAX_PAGES = 12  # 코스피200/코스닥150 모두 페이지당 종목 수 감안한 안전 상한


@dataclass
class Constituent:
    name: str
    code: str
    market: str  # "코스피200" | "코스닥150"


def _fetch_naver_index_constituents(naver_type: str, market_label: str) -> list[Constituent]:
    result: list[Constituent] = []
    seen_codes: set[str] = set()
    for page in range(1, _MAX_PAGES + 1):
        url = f"https://finance.naver.com/sise/entryJongmok.naver?type={naver_type}&page={page}"
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        links = soup.select("table.type_5 a.tltle")
        if not links:
            break

        new_on_this_page = 0
        for a in links:
            name = a.get_text(strip=True)
            href = a.get("href", "")
            if "code=" not in href or not name:
                continue
            code = href.split("code=")[-1]
            if code in seen_codes:
                continue
            seen_codes.add(code)
            result.append(Constituent(name=name, code=code, market=market_label))
            new_on_this_page += 1

        if new_on_this_page == 0:
            break

    return result


def get_kospi200() -> list[Constituent]:
    try:
        constituents = _fetch_naver_index_constituents("KPI200", "코스피200")
    except Exception as exc:
        print(f"[유니버스] 코스피200 조회 실패: {type(exc).__name__}: {exc}")
        return []
    if not constituents:
        print("[유니버스] 코스피200 조회 결과 0건 (페이지 구조 변경 가능성)")
    return constituents


def get_kosdaq150() -> list[Constituent]:
    try:
        constituents = _fetch_naver_index_constituents("KOSDAQ150", "코스닥150")
    except Exception as exc:
        print(f"[유니버스] 코스닥150 네이버 조회 실패: {type(exc).__name__}: {exc} — 폴백 파일 사용")
        constituents = []

    if constituents:
        return constituents

    print("[유니버스] 코스닥150 네이버 조회 0건 — 로컬 폴백 파일로 대체")
    return _load_kosdaq150_fallback()


def _load_kosdaq150_fallback() -> list[Constituent]:
    if not _KOSDAQ150_FALLBACK_PATH.exists():
        print(f"[유니버스] 폴백 파일 없음({_KOSDAQ150_FALLBACK_PATH.name}) — 코스닥150 종목 0건으로 진행")
        return []
    try:
        with open(_KOSDAQ150_FALLBACK_PATH, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("constituents", [])
        if not items:
            print(f"[유니버스] 폴백 파일이 비어있음({_KOSDAQ150_FALLBACK_PATH.name}) — 수동 등록 필요")
        return [Constituent(name=item["name"], code=item["code"], market="코스닥150") for item in items]
    except Exception as exc:
        print(f"[유니버스] 폴백 파일 파싱 실패: {type(exc).__name__}: {exc}")
        return []


def get_universe() -> list[Constituent]:
    return get_kospi200() + get_kosdaq150()
