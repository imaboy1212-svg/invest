"""공모주(IPO) 청약·상장 일정 조회 - 네이버증권 공모주 페이지.

IPO 팀은 이전까지 실제 일정 데이터 없이 일반 증권 뉴스에 공모주 얘기가
우연히 섞여 있길 기다리는 구조였다. 네이버증권 공모주 일정 페이지를 직접
조회해서 실제 예정된 청약/상장 일정을 근거 텍스트로 제공한다.
"""

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_ROWS = 10


def get_ipo_schedule_lines() -> list[str]:
    """다가오는 공모주 일정을 "종목명 / 상세정보..." 형태의 문자열 목록으로 반환. 실패 시 빈 리스트."""
    try:
        resp = requests.get("https://finance.naver.com/sise/ipo.naver", headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        return []

    table = soup.select_one("table.type_1")
    if table is None:
        return []

    rows = []
    for tr in table.select("tr"):
        cells = [td.get_text(strip=True) for td in tr.select("td")]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        rows.append(" / ".join(cells))
        if len(rows) >= MAX_ROWS:
            break

    if not rows:
        sample_text = table.get_text(" ", strip=True)[:300]
        print(f"[진단] IPO 일정표 파싱 0건: {sample_text}")
    return rows
