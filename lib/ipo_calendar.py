"""공모주(IPO) 청약·상장 일정 조회 - 네이버증권 공모주 페이지.

IPO 팀은 이전까지 실제 일정 데이터 없이 일반 증권 뉴스에 공모주 얘기가
우연히 섞여 있길 기다리는 구조였다. 네이버증권 공모주 일정 페이지를 직접
조회해서 실제 예정된 청약/상장 일정을 근거 텍스트로 제공한다.
"""

from io import StringIO

import pandas as pd
import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_ROWS = 10
_IPO_KEYWORDS = ("청약", "공모", "상장일", "종목명")


def get_ipo_schedule_lines() -> list[str]:
    """다가오는 공모주 일정을 "종목명 / 상세정보..." 형태의 문자열 목록으로 반환. 실패 시 빈 리스트.

    table.type_1 CSS 클래스로 직접 select 했더니 매칭 0건이었다(페이지 마크업이
    바뀌었거나 클래스명이 달라진 것으로 추정). 클래스명에 의존하지 않도록
    pandas.read_html 로 페이지의 모든 표를 파싱한 뒤, IPO 일정표로 보이는
    컬럼(청약/공모/상장일 등 키워드 포함)을 가진 표를 골라 사용한다.
    """
    try:
        resp = requests.get("https://finance.naver.com/sise/ipo.naver", headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
    except Exception as exc:
        print(f"[진단] IPO 페이지 요청 예외: {exc}")
        return []

    try:
        tables = pd.read_html(StringIO(resp.text))
    except Exception:
        tables = []

    target = None
    for df in tables:
        cols = " ".join(str(c) for c in df.columns)
        if any(kw in cols for kw in _IPO_KEYWORDS):
            target = df
            break

    if target is None:
        soup = BeautifulSoup(resp.text, "lxml")
        table_classes = [t.get("class") for t in soup.find_all("table")]
        sample_text = soup.get_text(" ", strip=True)[:300]
        print(
            f"[진단] IPO 일정표 파싱 0건: 표 {len(tables)}개 감지, "
            f"table class들={table_classes}, 본문={sample_text}"
        )
        return []

    rows = []
    for _, row in target.iterrows():
        cells = [str(v).strip() for v in row.tolist() if str(v).strip() and str(v).strip().lower() != "nan"]
        if len(cells) < 2:
            continue
        rows.append(" / ".join(cells))
        if len(rows) >= MAX_ROWS:
            break

    if not rows:
        print(f"[진단] IPO 일정표 파싱 0건(행없음): {target.to_string()[:300]}")
    return rows
