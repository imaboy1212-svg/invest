"""코스피200·코스닥150 구성종목 유니버스 조회.

코스피200은 네이버증권 entryJongmok.naver(type=KPI200)가 안정적으로 동작하는
것으로 확인됐다 (2026-07-28 실전 로그로 검증: table class는 type_1이고, 종목명
링크에 a.tltle 클래스가 없어 "code=" 포함 링크로 직접 추출하도록 셀렉터를 고침).

코스닥150은 네이버증권에 전용 조회 페이지 자체가 없다 — entryJongmok.naver에
type=KOSDAQ150, type=KDQ150 둘 다 시도해봤지만 둘 다 실제 데이터가 아니라 네이버의
범용 "일시적 오류로 페이지 접속이 불가합니다" 에러 페이지만 돌아온다(2026-07-28
실전 로그로 확인, IP 차단이 아니라 애초에 무효한 파라미터로 판단됨 — 같은 실행에서
바로 앞뒤로 호출한 코스피200/코스닥 시가총액 페이지는 정상 응답했음).

그래서 코스닥150 "지수 구성종목"을 정확히 가져오는 대신, **코스닥 시가총액 상위
150종목**(sise_market_sum.naver?sosok=1, 페이지당 50개 × 3페이지)으로 근사한다.
코스닥150 지수의 실제 구성 조건(유동성·거래대금 등)과 완전히 같지는 않지만, 이
스크리너의 목적(저평가+실적개선 스캔) 기준으로는 실질적으로 거의 동일한 대형·
중형 코스닥 종목군을 커버한다. 그래도 못 가져오면 로컬 백업 파일
(kosdaq150_constituents.json)로 최종 폴백한다.
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
        # 2026-07-28 실전 진단: table class는 type_5가 아니라 type_1이고, 종목명 링크에
        # a.tltle 클래스가 없다. "code=" 포함 링크로 직접 찾는 게 구조 변경에 안전하다.
        links = [a for a in soup.select("table.type_1 a") if "code=" in a.get("href", "")]
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


_KOSDAQ_MARKET_CAP_PAGES = 3  # 페이지당 50종목 × 3 = 150종목(코스닥150 근사)


def _fetch_kosdaq_market_cap_top(pages: int = _KOSDAQ_MARKET_CAP_PAGES) -> list[Constituent]:
    """코스닥 시가총액 상위 종목으로 코스닥150을 근사한다 (entryJongmok.naver에
    코스닥150 전용 페이지가 없어서 쓰는 대체 소스, lib/universe.py 모듈 docstring 참고)."""
    result: list[Constituent] = []
    seen_codes: set[str] = set()
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok=1&page={page}"
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        links = soup.select("table.type_2 a.tltle")
        if not links:
            break
        for a in links:
            name = a.get_text(strip=True)
            href = a.get("href", "")
            if "code=" not in href or not name:
                continue
            code = href.split("code=")[-1]
            if code in seen_codes:
                continue
            seen_codes.add(code)
            result.append(Constituent(name=name, code=code, market="코스닥150"))
    return result


def get_kosdaq150() -> list[Constituent]:
    try:
        constituents = _fetch_kosdaq_market_cap_top()
    except Exception as exc:
        print(f"[유니버스] 코스닥150(시가총액 상위 근사) 조회 실패: {type(exc).__name__}: {exc} — 폴백 파일 사용")
        constituents = []

    if constituents:
        return constituents

    print("[유니버스] 코스닥150(시가총액 상위 근사) 조회 0건 — 로컬 폴백 파일로 대체")
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
