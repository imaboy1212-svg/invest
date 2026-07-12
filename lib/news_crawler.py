"""증권 뉴스 헤드라인 크롤링.

네이버증권 뉴스(여러 언론사를 한 페이지에 모아 놓아 셀렉터가 비교적 안정적)를
우선 소스로 쓰고, 개별 언론사 사이트를 폴백으로 둔다.
전부 실패하면 빈 리스트를 반환하고, 호출부(topic_recommender.py)가 리포트에
"뉴스 수집 실패, 지수 데이터만 반영"을 명시한다.

주의: 각 사이트의 정확한 마크업은 이 자동화 환경의 네트워크 정책상 직접
접속/검증이 불가능해 확정하지 못했다. 셀렉터 대신 기사 링크 패턴 + 텍스트
길이로 헤드라인을 골라내는 휴리스틱을 쓰고, 0건일 때는 실제 href 샘플을
로그로 남겨 다음 반복에서 패턴을 조정할 수 있게 한다.
"""

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0"}
MIN_HEADLINE_LEN = 8
MAX_HEADLINES_PER_SITE = 10
MIN_SOURCES_REQUIRED = 2
SUMMARY_LIMIT_PER_SITE = 8  # 기사 본문 요약(og:description)을 가져올 헤드라인 수 상한 (전체 요청 시간 제한용)

# 네이버증권 뉴스: 여러 언론사 기사를 한 페이지에 모아두는 아그리게이터.
# 개별 기사 언론사명은 각 항목의 .press 클래스에서 뽑아내고, 못 찾으면 "네이버증권"으로 표시한다.
_NAVER_FINANCE_SITES = [
    {"name": "네이버증권-주요뉴스", "url": "https://finance.naver.com/news/mainnews.naver"},
]

# 개별 언론사 사이트 (아그리게이터가 전부 실패했을 때 폴백)
_PRESS_SITES = [
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

_NAVER_ARTICLE_PATTERN = re.compile(r"/news/news_read\.naver")


@dataclass
class Headline:
    source: str
    title: str
    url: str
    summary: str = ""


def _fetch_article_summary(url: str) -> str:
    """기사 페이지의 og:description(또는 description) 메타 태그로 본문 요약을 가져온다.

    대부분의 국내 뉴스 사이트가 SEO용으로 이 메타 태그에 기사 리드문단 요약을 넣어두므로,
    본문 전체를 파싱하는 것보다 훨씬 안정적이다. 실패하면 빈 문자열(제목만 사용).
    """
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=6)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        tag = soup.select_one('meta[property="og:description"]') or soup.select_one('meta[name="description"]')
        if tag and tag.get("content"):
            return tag["content"].strip()
    except Exception:
        pass
    return ""


def _attach_summaries(headlines: list[Headline]) -> None:
    for headline in headlines[:SUMMARY_LIMIT_PER_SITE]:
        headline.summary = _fetch_article_summary(headline.url)


def _crawl_naver_finance(site: dict) -> list[Headline]:
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

        if not _NAVER_ARTICLE_PATTERN.search(href):
            continue
        if len(title) < MIN_HEADLINE_LEN:
            continue
        full_url = requests.compat.urljoin(site["url"], href)
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        press = "네이버증권"
        container = a.find_parent(["dl", "li", "div"])
        if container:
            press_tag = container.select_one(".press")
            if press_tag:
                press = press_tag.get_text(strip=True)

        headlines.append(Headline(source=press, title=title, url=full_url))
        if len(headlines) >= MAX_HEADLINES_PER_SITE:
            break

    if not headlines:
        print(f"[진단] {site['name']} href 샘플(텍스트 8자 이상, 최대 15개): {all_hrefs_with_text[:15]}")
    _attach_summaries(headlines)
    return headlines


def _crawl_press_site(site: dict) -> list[Headline]:
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
        print(f"[진단] {site['name']} href 샘플(텍스트 8자 이상, 최대 15개): {all_hrefs_with_text[:15]}")
    _attach_summaries(headlines)
    return headlines


def collect_headlines() -> tuple[list[Headline], list[str]]:
    """(수집된 헤드라인 리스트, 성공한 언론사/소스명 리스트) 반환.

    최소 2개 언론사(또는 소스) 확인을 목표로, 네이버증권 아그리게이터를 먼저 시도하고
    실패하면 개별 언론사 사이트로 폴백한다.
    """
    all_headlines: list[Headline] = []
    succeeded_sources: set[str] = set()

    for site in _NAVER_FINANCE_SITES:
        try:
            headlines = _crawl_naver_finance(site)
        except Exception as exc:
            print(f"[뉴스크롤링 실패] {site['name']}: {type(exc).__name__}: {exc}")
            continue
        if not headlines:
            print(f"[뉴스크롤링 0건] {site['name']}: 페이지는 받았으나 기사 링크 패턴 매칭 없음")
            continue
        all_headlines.extend(headlines)
        succeeded_sources.update(h.source for h in headlines)
        if len(succeeded_sources) >= MIN_SOURCES_REQUIRED:
            return all_headlines, sorted(succeeded_sources)

    for site in _PRESS_SITES:
        try:
            headlines = _crawl_press_site(site)
        except Exception as exc:
            print(f"[뉴스크롤링 실패] {site['name']}: {type(exc).__name__}: {exc}")
            continue
        if not headlines:
            print(f"[뉴스크롤링 0건] {site['name']}: 페이지는 받았으나 href_pattern에 걸린 링크 없음")
            continue
        all_headlines.extend(headlines)
        succeeded_sources.add(site["name"])
        if len(succeeded_sources) >= MIN_SOURCES_REQUIRED:
            break

    return all_headlines, sorted(succeeded_sources)
