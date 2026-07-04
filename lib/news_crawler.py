"""증권 뉴스 헤드라인 크롤링.

우선순위: 한국경제 → 매일경제 → 서울경제 → 파이낸셜뉴스.
최소 2개 언론사 확인을 목표로 하되, 한 사이트가 실패하면 다음 우선순위로 넘어간다.
전부 실패하면 빈 리스트를 반환하고, 호출부(topic_recommender.py)가 리포트에
"뉴스 수집 실패, 지수 데이터만 반영"을 명시한다.

주의: 각 언론사 페이지의 정확한 CSS 셀렉터는 이 자동화 환경의 네트워크 정책상
직접 접속/검증이 불가능해 확정하지 못했다. 셀렉터 대신 기사 링크 패턴(href에 숫자
기사 ID 포함) + 본문 텍스트 길이로 헤드라인을 골라내는 휴리스틱을 사용한다.
실제 운영 중 특정 사이트에서 헤드라인이 비정상적으로 수집되면 해당 사이트의
ARTICLE_HREF_PATTERN을 조정해야 한다.
"""

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0"}
MIN_HEADLINE_LEN = 8
MAX_HEADLINES_PER_SITE = 8
MIN_SOURCES_REQUIRED = 2

_SITES = [
    {
        "name": "한국경제",
        "url": "https://www.hankyung.com/",
        "href_pattern": re.compile(r"/article/\d+"),
    },
    {
        "name": "매일경제",
        "url": "https://stock.mk.co.kr/",
        "href_pattern": re.compile(r"/news/stock-market/\d+|/news/\d+"),
    },
    {
        "name": "서울경제",
        "url": "https://www.sedaily.com/NewsList/GD07",
        "href_pattern": re.compile(r"/NewsView/\w+"),
    },
    {
        "name": "파이낸셜뉴스",
        "url": "https://www.fnnews.com/section/007001000000",
        "href_pattern": re.compile(r"/news/\d+"),
    },
]


@dataclass
class Headline:
    source: str
    title: str
    url: str


def _crawl_site(site: dict) -> list[Headline]:
    resp = requests.get(site["url"], headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    seen_urls = set()
    headlines: list[Headline] = []
    all_hrefs_with_text = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(strip=True)
        if len(title) >= MIN_HEADLINE_LEN:
            all_hrefs_with_text.append(href)

        if not site["href_pattern"].search(href):
            continue
        if len(title) < MIN_HEADLINE_LEN:
            continue
        full_url = href if href.startswith("http") else requests.compat.urljoin(site["url"], href)
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        headlines.append(Headline(source=site["name"], title=title, url=full_url))
        if len(headlines) >= MAX_HEADLINES_PER_SITE:
            break

    if not headlines:
        sample = all_hrefs_with_text[:15]
        print(f"[진단] {site['name']} href 샘플(텍스트 8자 이상, 최대 15개): {sample}")
    return headlines


def collect_headlines() -> tuple[list[Headline], list[str]]:
    """(수집된 헤드라인 리스트, 성공한 언론사명 리스트) 반환.

    최소 2개 언론사 성공을 목표로 우선순위 순서대로 계속 시도한다.
    """
    all_headlines: list[Headline] = []
    succeeded_sources: list[str] = []

    for site in _SITES:
        try:
            headlines = _crawl_site(site)
        except Exception as exc:
            print(f"[뉴스크롤링 실패] {site['name']}: {type(exc).__name__}: {exc}")
            continue
        if not headlines:
            print(f"[뉴스크롤링 0건] {site['name']}: 페이지는 받았으나 href_pattern에 걸린 링크 없음")
            continue
        all_headlines.extend(headlines)
        succeeded_sources.append(site["name"])
        if len(succeeded_sources) >= MIN_SOURCES_REQUIRED:
            break

    return all_headlines, succeeded_sources
