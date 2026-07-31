"""1회성 진단 스크립트 — lib/fundamentals.py의 PER/EPS/PBR/BPS 파싱이 전부 같은
숫자(2,026.03)를 반환하는 버그의 원인을 확인한다.

lib/market_data.py의 52주 고저 파싱 버그를 고칠 때 썼던
scratch_diagnose_52w.py와 같은 목적 — 실제 페이지 구조를 workflow_dispatch
로그로 직접 확인한 뒤 lib/fundamentals.py를 고치고 이 파일은 삭제한다.
"""

import re

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0"}


def main() -> None:
    code = "005930"
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    resp = requests.get(url, headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    tab_con1 = soup.select_one("#tab_con1")
    per_table = soup.select_one("table.per_table")
    print(f"[진단] #tab_con1 존재: {tab_con1 is not None}")
    print(f"[진단] table.per_table 존재: {per_table is not None}")

    if tab_con1 is not None:
        text = tab_con1.get_text(" ", strip=True)
        print(f"[진단] #tab_con1 전체 길이: {len(text)}자")
        print(f"[진단] #tab_con1 앞부분 500자: {text[:500]!r}")

        for label in ("PER", "EPS", "PBR", "BPS", "동일업종", "배당수익률"):
            positions = [m.start() for m in re.finditer(re.escape(label), text)]
            print(f"[진단] '{label}' 등장 위치: {positions[:10]} (총 {len(positions)}회)")
            if positions:
                first = positions[0]
                print(f"[진단]   첫 등장 주변 텍스트: {text[max(0, first - 20):first + 60]!r}")

    if per_table is not None:
        pt_text = per_table.get_text(" ", strip=True)
        print(f"[진단] table.per_table 전체 텍스트: {pt_text!r}")


if __name__ == "__main__":
    main()
