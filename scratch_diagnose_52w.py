"""52주 최고/최저 조회 실패 원인 진단용 1회성 스크립트.

PythonAnywhere invest-screener 디렉토리에서 실행:
    python3 scratch_diagnose_52w.py
현재가(.no_today .blind)는 이미 성공하는 게 확인됐으니, 이번엔 "52주" 텍스트가
페이지 어디에 어떤 형태로 있는지(혹은 아예 없는지)를 확인한다.
"""

import re

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0"}
CODE = "005930"  # 삼성전자 — 데이터가 확실히 존재하는 종목

url = f"https://finance.naver.com/item/main.naver?code={CODE}"
resp = requests.get(url, headers=HEADERS, timeout=10)
print("status_code:", resp.status_code)
print("응답(HTML) 길이:", len(resp.text))

soup = BeautifulSoup(resp.text, "lxml")
flat_text = soup.get_text(" ", strip=True)
print("flat_text 길이:", len(flat_text))

# "52주"가 텍스트 어디에 있는지 전부 찾아서 앞뒤 문맥 출력
matches = list(re.finditer("52주", flat_text))
print(f"\n'52주' 등장 횟수: {len(matches)}")
for m in matches[:10]:
    start = max(0, m.start() - 30)
    end = min(len(flat_text), m.end() + 30)
    print("  ...", flat_text[start:end], "...")

# 혹시 다른 표현(최고가/최저가/고가/저가)을 쓰는지도 확인
for keyword in ["최고가", "최저가", "고가", "저가", "52주최고", "52주최저"]:
    count = flat_text.count(keyword)
    print(f"'{keyword}' 등장 횟수: {count}")

# 원본 HTML(태그 포함)에도 "52주"가 있는지 확인 - 없으면 JS로 나중에 채워지는 부분일 가능성
print("\n원본 HTML에 '52주' 포함 여부:", "52주" in resp.text)

# 실제 "투자정보" 관련 테이블/영역을 클래스명으로 찾아보기
for cls in ["aside_invest_info", "tb_type1", "first"]:
    found = soup.select(f".{cls}")
    print(f"class={cls} 개수: {len(found)}")
    if found:
        print("   ", found[0].get_text(" ", strip=True)[:300])
