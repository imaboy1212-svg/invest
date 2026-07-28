"""코스피200/코스닥150 유니버스 크롤링 진단용 1회성 스크립트.

PythonAnywhere invest-screener 디렉토리에서 다음처럼 실행:
    python3 scratch_diagnose_universe.py
출력 결과를 그대로 복사해서 알려주면 lib/universe.py의 셀렉터를 실제 구조에 맞게 고칠 수 있다.
"""

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0"}


def check(label: str, url: str) -> None:
    print(f"\n===== {label} =====")
    print("URL:", url)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        print("status_code:", resp.status_code)
        print("응답 길이:", len(resp.text))
        soup = BeautifulSoup(resp.text, "lxml")

        tables = soup.find_all("table")
        print(f"table 태그 개수: {len(tables)}")
        for i, t in enumerate(tables[:5]):
            print(f"  table[{i}] class={t.get('class')}")

        tltle_links = soup.select("a.tltle")
        print(f"a.tltle 개수(클래스 무관 전체): {len(tltle_links)}")
        for a in tltle_links[:5]:
            print("   ", a.get_text(strip=True), a.get("href"))

        # code= 패턴을 가진 링크 아무거나 찾아보기 (구조가 바뀌었어도 이건 남아있을 가능성)
        code_links = [a for a in soup.find_all("a", href=True) if "code=" in a["href"]]
        print(f"'code=' 포함 링크 개수: {len(code_links)}")
        for a in code_links[:8]:
            print("   ", a.get_text(strip=True), a.get("href"))

    except Exception as exc:
        print("요청 실패:", type(exc).__name__, exc)


check("코스피200 (type=KPI200) - 확인용, 이미 정상 동작 확인됨", "https://finance.naver.com/sise/entryJongmok.naver?type=KPI200&page=1")

# 코스닥150(type=KOSDAQ150)은 200 응답이지만 빈 페이지였다. entryJongmok.naver가
# 실제로 지원하는 type 값이 무엇인지 몇 가지 후보를 더 찔러본다.
for candidate in ["KOSDAQ150", "KQ150", "KDQ150", "KOSDAQ", "150"]:
    check(f"코스닥150 후보 (type={candidate})", f"https://finance.naver.com/sise/entryJongmok.naver?type={candidate}&page=1")

# 네이버증권 지수 상세 페이지 쪽에 구성종목 탭이 있는지도 확인
check(
    "코스닥150 지수 상세 페이지(sise_index_detail)",
    "https://finance.naver.com/sise/sise_index_detail.naver?code=KOSDAQ150",
)
