"""WP투자정보본부 주제 후보 자동화 파이프라인 진입점.

실행 흐름 (WP투자토픽자동화가이드.md 4장 그대로):
1) 날짜/요일 검증  2) 지수 조회+교차검증  3) 뉴스 크롤링
4) Gemini로 주제 후보 3건 + 상세 브리핑 생성  5) 완료 주제 필터링
6) 브리핑 마크다운 파일 생성  7) 텔레그램 전송
"""

import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from lib import (
    calendar_utils,
    completed_topics,
    gemini_client,
    ipo_calendar,
    market_data,
    news_crawler,
    recent_picks,
    stock_discovery,
    telegram_client,
)

BRIEFINGS_DIR = Path(__file__).resolve().parent / "briefings"
LOGS_DIR = Path(__file__).resolve().parent / "logs"

TEAM_ORDER = ["종목리포트", "마켓칼럼", "IPO"]


def _log(lines: list[str]) -> None:
    print("\n".join(lines))
    LOGS_DIR.mkdir(exist_ok=True)
    log_path = LOGS_DIR / f"{datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()}.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")


def _build_run_note(run_ctx) -> str | None:
    if run_ctx.mode == "weekend":
        return (
            f"{run_ctx.note}. 주말 사이 상위종목 이슈·경제 뉴스가 있으면 금요일 데이터보다 "
            "그 뉴스를 우선 반영하고, '월요일에 주목해야 할 이슈' 관점으로 주제를 구성할 것. "
            "주말 뉴스가 없으면 금요일 종가 기준으로만 구성할 것."
        )
    return run_ctx.note


_INDEX_WORDS = ("코스피", "코스닥", "KOSPI", "KOSDAQ")


def _strip_index_mentions(text: str) -> bool:
    """텍스트에 지수 언급이 있으면 True (종목리포트에서 제외 판단용)."""
    return any(word in text for word in _INDEX_WORDS)


def _match_known_code(text: str, known_codes: dict[str, str]) -> tuple[str, str] | None:
    """known_codes(네이버에서 이미 확보한 종목명→코드) 중 text에 등장하는 가장 긴 이름을 찾는다."""
    for name in sorted(known_codes, key=len, reverse=True):
        if len(name) >= 2 and name in text:
            return name, known_codes[name]
    return None


def _enforce_stock_report_figures(topic: dict, data_date, known_codes: dict[str, str]) -> None:
    """종목리포트에서 지수(코스피/코스닥) 언급을 완전히 배제하고 종목 자체 심층 수치로 채운다.

    Gemini가 지수 수치를 종목리포트에 섞어 넣는 문제가 있어, key_figures뿐 아니라
    서론/본론/추천사유에서도 지수 언급 항목을 제거하고, pykrx 실측 수급/밸류에이션
    데이터를 최대한 채운다 (등락률, 외국인/기관 순매수, 시가총액, PER/PBR/EPS).

    종목코드는 가능하면 known_codes(네이버 인기종목 발굴 단계에서 이미 확보한 코드)를
    우선 쓴다. pykrx 종목명→코드 매핑은 이 실행 환경에서 KRX가 계속 차단해 실패하는
    경우가 많아서, 그 경우 가격 조회 자체가 안 돼 Gemini가 뉴스 헤드라인에 있던
    (문맥이 잘린) 숫자를 대신 써버리는 문제가 실제로 발생했다.
    """
    confirmed_figures = []
    match = _match_known_code(topic["name"], known_codes) or market_data.find_mentioned_ticker(topic["name"])
    if match:
        name, code = match
        quote = market_data.get_stock_snapshot(name, data_date, code=code)
        if quote and quote.adopted is not None:
            # 소스 하나만 조용히 채택해서 보여주면 스크래핑 오류로 가격이 틀려도 알아챌 수
            # 없다. 지수와 동일하게 KRX/네이버/Yahoo 값을 전부 보여줘서 코로 님이 직접
            # 눈으로 대조할 수 있게 한다 (가이드 4-2 교차검증 원칙과 동일).
            confirmed_figures.append({"figure": quote.display_line(), "source": "교차검증"})
            prices = [p for p in (quote.krx, quote.naver, quote.yahoo) if p is not None]
            if len(prices) >= 2 and (max(prices) - min(prices)) / min(prices) > 0.05:
                print(
                    f"[가격불일치] {name}: KRX={quote.krx} 네이버={quote.naver} "
                    f"Yahoo={quote.yahoo} (5% 이상 차이 — 스크래핑 오류 가능성)"
                )
        else:
            print(f"[가격조회실패] {name}({code}): KRX/네이버/Yahoo 전부 조회 실패, 핵심 수치에서 가격 항목 제외됨")

        supplementary = market_data.get_supplementary_data(name, data_date, code=code)
        if supplementary.foreign_net_buy is not None:
            confirmed_figures.append({
                "figure": f"{name} 외국인 순매수 {supplementary.foreign_net_buy:,.0f}",
                "source": "pykrx",
            })
        if supplementary.institution_net_buy is not None:
            confirmed_figures.append({
                "figure": f"{name} 기관 순매수 {supplementary.institution_net_buy:,.0f}",
                "source": "pykrx",
            })
        if supplementary.market_cap is not None:
            confirmed_figures.append({
                "figure": f"{name} 시가총액 {supplementary.market_cap:,.0f}원",
                "source": "pykrx",
            })
        if supplementary.per is not None:
            confirmed_figures.append({"figure": f"{name} PER {supplementary.per:.2f}배", "source": "pykrx"})
        if supplementary.pbr is not None:
            confirmed_figures.append({"figure": f"{name} PBR {supplementary.pbr:.2f}배", "source": "pykrx"})
        if supplementary.eps is not None:
            confirmed_figures.append({"figure": f"{name} EPS {supplementary.eps:,.0f}원", "source": "pykrx"})

    filtered_gemini_figures = [
        kf for kf in topic.get("key_figures", []) if not _strip_index_mentions(kf.get("figure", ""))
    ]
    topic["key_figures"] = (confirmed_figures + filtered_gemini_figures)[:10]

    structure = topic.get("article_structure", {})
    if _strip_index_mentions(structure.get("intro_angle", "")):
        structure["intro_angle"] = ""
    structure["body_points"] = [
        point for point in structure.get("body_points", []) if not _strip_index_mentions(point)
    ]
    topic["article_structure"] = structure


def _briefing_markdown(topic: dict, run_note: str | None, extra_quote_line: str | None) -> str:
    lines = [f"# [{topic['team']}] {topic['name']}", ""]
    if run_note:
        lines += [f"> {run_note}", ""]
    if topic.get("golden_time"):
        lines += ["**⚡ 골든타임**", ""]

    lines.append("## 핵심 수치")
    for kf in topic.get("key_figures", []):
        lines.append(f"- {kf['figure']} (출처: {kf['source']})")
    if extra_quote_line:
        lines.append(f"- {extra_quote_line}")
    lines.append("")

    lines.append("## 관련 뉴스")
    for news in topic.get("related_news", []):
        # Gemini가 headline에 "[언론사] 제목"을 통째로 넣어 source와 중복 표기되는 경우가
        # 있어, 앞의 대괄호 태그를 방어적으로 제거한다.
        headline = re.sub(r"^\[[^\]]+\]\s*", "", news["headline"])
        lines.append(f"- [{news['source']}] {headline}")
    lines.append("")

    structure = topic.get("article_structure", {})
    lines.append("## 예상 기사 구조")
    if structure.get("intro_angle"):
        lines.append(f"- 서론 각도: {structure['intro_angle']}")
    for point in structure.get("body_points", []):
        lines.append(f"- 본론 관점: {point}")
    lines.append("")

    lines.append(f"## 추천 사유\n{topic.get('reason', '')}\n")
    return "\n".join(lines)


def _summary_message(
    run_ctx,
    index_snapshot,
    topics: list[dict],
    excluded: list[dict],
    news_failed: bool,
    rejected_teams: set[str],
) -> str:
    header = f"📊 WP투자 주제 후보 ({run_ctx.now_kst.strftime('%Y-%m-%d %H:%M')} 기준)"
    lines = [header, ""]
    if run_ctx.note:
        lines += [f"※ {run_ctx.note}", ""]

    for label, quote in index_snapshot.items():
        tag = " ⚡골든타임" if quote.golden_time else ""
        lines.append(quote.display_line() + tag)
    lines.append("")

    by_team = {t["team"]: t for t in topics}
    for team in TEAM_ORDER:
        topic = by_team.get(team)
        if not topic:
            if team in rejected_teams:
                lines.append(f"[{team}] 검증 실패로 제외됨 (원문에 없는 회사명·수치 감지)")
            else:
                lines.append(f"[{team}] 확인된 수치 부족으로 이번 회차 미생성")
            continue
        golden = " ⚡골든타임" if topic.get("golden_time") else ""
        first_figure = topic["key_figures"][0] if topic.get("key_figures") else None
        lines.append(f"[{team}] {topic['name']}{golden}")
        if first_figure:
            lines.append(f"- 확인 수치: {first_figure['figure']}, 출처 {first_figure['source']}")
        lines.append(f"- 사유: {topic.get('reason', '')}")
        lines.append("")

    if news_failed:
        lines.append("※ 뉴스 수집 실패, 지수 데이터만 반영")
    if excluded:
        lines.append(f"※ 완료 주제 {len(excluded)}건 자동 제외됨")
    lines.append(f"※ 상세 브리핑 파일 {len(topics)}건 첨부")
    return "\n".join(lines)


def main() -> int:
    load_dotenv()

    run_ctx = calendar_utils.get_run_context()
    run_note = _build_run_note(run_ctx)
    log_lines = [f"[실행] mode={run_ctx.mode} data_date={run_ctx.data_date} note={run_ctx.note}"]

    index_snapshot = market_data.get_index_snapshot(run_ctx.data_date)
    index_lines = [q.display_line() for q in index_snapshot.values()]
    log_lines.append("[지수] " + " | ".join(index_lines))

    global_lines = market_data.get_global_market_lines()
    log_lines.append(f"[해외지수] {len(global_lines)}건 조회")

    headlines, succeeded_sources = news_crawler.collect_headlines()
    news_failed = len(succeeded_sources) == 0
    news_lines = [
        f"[{h.source}] {h.title}" + (f" - {h.summary}" if h.summary else "") for h in headlines
    ]
    log_lines.append(f"[뉴스] 성공 언론사={succeeded_sources} 헤드라인 {len(headlines)}건")

    recent_stock_names = recent_picks.get_recent_names(run_ctx.data_date)
    log_lines.append(f"[최근종목] 쿨다운 중({recent_picks.COOLDOWN_DAYS}일) 제외 대상: {sorted(recent_stock_names)}")

    stock_candidates = stock_discovery.discover_candidates(run_ctx.data_date, recent_stock_names)
    known_codes = {c.name: c.code for c in stock_candidates}
    stock_candidate_lines = [c.summary_line() for c in stock_candidates[:10]]
    log_lines.append(f"[종목발굴] 후보 {len(stock_candidates)}건 (상위: {stock_candidate_lines[:5]})")
    for c in stock_candidates[:10]:
        news_lines.extend(c.news_headlines)

    ipo_lines = ipo_calendar.get_ipo_schedule_lines()
    log_lines.append(f"[IPO일정] {len(ipo_lines)}건 조회")

    raw_topics, rejected = gemini_client.generate_topics(
        run_note, index_lines, news_lines, stock_candidate_lines, ipo_lines, global_lines, recent_stock_names
    )
    log_lines.append(f"[Gemini] 생성된 주제 {len(raw_topics)}건, 검증 실패 {len(rejected)}건")
    for r in rejected:
        log_lines.append(f"[검증실패] {r['team']} - {r['name']}: {r['reason']}")
    rejected_teams = {r["team"] for r in rejected}

    new_topics, excluded = completed_topics.filter_new_topics(raw_topics)
    log_lines.append(f"[필터링] 신규 {len(new_topics)}건 / 완료주제 제외 {len(excluded)}건")

    BRIEFINGS_DIR.mkdir(exist_ok=True)
    date_str = run_ctx.now_kst.date().isoformat()
    briefing_paths = []
    for topic in new_topics:
        extra_quote_line = None
        if topic["team"] == "종목리포트":
            _enforce_stock_report_figures(topic, run_ctx.data_date, known_codes)
            match = _match_known_code(topic["name"], known_codes) or market_data.find_mentioned_ticker(
                topic["name"]
            )
            if match:
                recent_picks.record_pick(match[0], run_ctx.data_date)
                log_lines.append(f"[최근종목기록] {match[0]} 쿨다운 등록")
        else:
            match = _match_known_code(topic["name"], known_codes) or market_data.find_mentioned_ticker(
                topic["name"]
            )
            if match:
                name, code = match
                quote = market_data.get_stock_snapshot(name, run_ctx.data_date, code=code)
                if quote and quote.adopted is not None:
                    extra_quote_line = quote.display_line()

        content = _briefing_markdown(topic, run_note, extra_quote_line)
        path = BRIEFINGS_DIR / f"{date_str}-{topic['team']}.md"
        path.write_text(content, encoding="utf-8")
        briefing_paths.append(path)
    log_lines.append(f"[브리핑] 파일 {len(briefing_paths)}건 생성: {[p.name for p in briefing_paths]}")

    summary = _summary_message(run_ctx, index_snapshot, new_topics, excluded, news_failed, rejected_teams)
    telegram_client.send_message(summary)
    for path in briefing_paths:
        telegram_client.send_document(path)
    log_lines.append("[텔레그램] 요약 메시지 + 브리핑 파일 전송 완료")

    _log(log_lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
