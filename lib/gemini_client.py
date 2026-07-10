"""Gemini API로 팀별 주제 후보 3건(종목리포트/마켓칼럼/IPO 각 1건) + 상세 브리핑 생성.

가이드 4-4 원칙: 수치가 확인되지 않은 주제는 생성하지 않는다(추측 금지).
확인되지 않으면 해당 팀 주제를 아예 만들지 않아도 된다(3건 미만 가능).

환각(hallucination) 방지: Gemini가 제공된 원문(지수/뉴스/종목 후보 텍스트)에 없는
회사명·숫자를 지어내는 사고가 있었음 (2026-07-09). 프롬프트에 원문 강제 인용
규칙을 명시하고, 응답을 받은 뒤 회사명·숫자가 실제로 원문에 등장하는지 코드
레벨에서 재검증해서 통과하지 못한 후보는 제외한다.
"""

import json
import os
import re

from google import genai
from google.genai import types

from lib import market_data

MODEL_NAME = "gemini-2.5-flash"

_RESPONSE_SCHEMA_HINT = """
다음 JSON 형식으로만 응답하라 (설명 문장 없이 JSON만):
{
  "topics": [
    {
      "team": "종목리포트 | 마켓칼럼 | IPO 중 하나",
      "name": "주제명 (구체적 고유명사 포함)",
      "golden_time": true or false,
      "reason": "추천 사유 1줄",
      "key_figures": [
        {"figure": "핵심 수치 설명", "source": "출처"}
      ],
      "related_news": [
        {"headline": "관련 뉴스 헤드라인", "source": "언론사"}
      ],
      "article_structure": {
        "intro_angle": "서론 각도 1줄",
        "body_points": ["본론에서 다룰 관점 1", "본론에서 다룰 관점 2"]
      }
    }
  ]
}
key_figures는 3~5개, related_news는 2~3개로 채워라.
확인된 수치·뉴스가 부족해 특정 팀(종목리포트/마켓칼럼/IPO) 주제를 만들 수 없으면
그 팀은 topics 배열에서 아예 제외하라. 존재하지 않는 수치나 뉴스를 지어내지 마라.

중요 — 팀별 key_figures 구분 규칙:
- '종목리포트' team의 key_figures는 반드시 해당 개별 종목 자체에 대한 수치만 넣어라
  (그 종목의 등락률, 거래대금, 수급, 시가총액, 실적 등). 코스피/코스닥 지수 수치는
  '종목리포트' key_figures에 절대 넣지 마라.
- 코스피/코스닥 지수 수치는 '마켓칼럼' team의 key_figures에서만 사용하라.
- '종목리포트'의 서론/본론에서 지수 급락 등 시장 전체 상황을 배경으로 언급하는 것은
  괜찮지만, key_figures 항목 자체는 그 종목 고유 수치로만 채워야 한다.

절대 규칙 — 원문 밖 내용 생성 금지 (환각 금지):
- related_news의 headline은 아래 제공된 [증권 뉴스 헤드라인] 목록에 있는 헤드라인을
  그대로(글자 단위로 동일하게) 옮겨 적어라. 문장을 다듬거나 요약하거나 새로 만들지 마라.
- key_figures, name, reason에 들어가는 회사명·금액·비율·계약규모 등 모든 숫자와
  고유명사는 반드시 아래 제공된 원문([지수 현황]/[증권 뉴스 헤드라인]/[종목리포트 후보
  종목])에 등장하는 것만 사용하라. 원문에 없는 회사명, 금액, 수치는 절대로 만들어내지
  마라 (예: 헤드라인에 언급되지 않은 계약 상대방 회사명을 지어내는 것 금지).
- 원문만으로 특정 수치나 이름을 확인할 수 없으면, 그 항목은 비워두거나 "확인 필요"라고
  적어라. 그럴듯하게 추정해서 채우지 마라.
"""


def _build_prompt(
    run_note: str | None,
    index_lines: list[str],
    news_lines: list[str],
    stock_candidate_lines: list[str],
) -> str:
    context_lines = []
    if run_note:
        context_lines.append(f"[실행 기준] {run_note}")
    context_lines.append("[지수 현황]")
    context_lines.extend(index_lines)
    context_lines.append("[증권 뉴스 헤드라인]")
    if news_lines:
        context_lines.extend(news_lines)
    else:
        context_lines.append("(뉴스 수집 실패 - 지수 데이터만 활용)")
    if stock_candidate_lines:
        context_lines.append("[종목리포트 후보 종목 - 네이버증권 인기종목 중 뉴스 확인된 종목]")
        context_lines.extend(stock_candidate_lines)
        context_lines.append(
            "종목리포트 주제는 가능하면 위 후보 종목 중에서 고르되, 확인된 뉴스나 수치 근거가 "
            "있는 종목을 우선하라. 후보에 없어도 뉴스에 확실한 근거가 있는 종목이면 사용해도 된다. "
            "후보 종목이라도 관련 기사·공시가 실제로 없으면 억지로 종목리포트를 만들지 말고 "
            "그 팀은 비워라."
        )

    return (
        "너는 증권 매체의 데스크다. 아래 확인된 지수/뉴스 데이터만 근거로 삼아 "
        "'종목리포트', '마켓칼럼', 'IPO' 세 팀에 각각 1건씩 기사 주제 후보를 만들어라.\n\n"
        + "\n".join(context_lines)
        + "\n\n"
        + _RESPONSE_SCHEMA_HINT
    )


_NUMBER_RE = re.compile(r"\d[\d,]*")


def _number_tokens(text: str) -> set[str]:
    return {tok.replace(",", "") for tok in _NUMBER_RE.findall(text) if len(tok.replace(",", "")) >= 2}


def _verify_topic(topic: dict, grounding_text: str, grounding_numbers: set[str]) -> str | None:
    """원문 밖 회사명·숫자 생성(환각) 여부를 단순 포함 검사로 확인한다.

    문제없으면 None, 문제가 있으면 실패 사유 문자열을 반환한다.
    """
    for news in topic.get("related_news", []):
        headline = news.get("headline", "")
        if headline and headline not in grounding_text:
            return f"관련 뉴스 헤드라인이 원문에 없음: {headline!r}"

    for kf in topic.get("key_figures", []):
        figure_text = kf.get("figure", "")
        for number in _number_tokens(figure_text):
            if number not in grounding_numbers:
                return f"핵심 수치의 숫자가 원문에 없음: {number!r} (수치 설명: {figure_text!r})"

    if topic.get("team") == "종목리포트" and market_data.ticker_map_available():
        if market_data.find_mentioned_ticker(topic.get("name", "")) is None:
            return f"주제명에서 실제 상장 종목명을 찾을 수 없음: {topic.get('name')!r}"

    return None


def generate_topics(
    run_note: str | None,
    index_lines: list[str],
    news_lines: list[str],
    stock_candidate_lines: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """(검증 통과한 주제 목록, 검증 실패로 제외된 주제 목록[team/name/reason]) 반환."""
    stock_candidate_lines = stock_candidate_lines or []
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = _build_prompt(run_note, index_lines, news_lines, stock_candidate_lines)
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    data = json.loads(response.text)
    raw_topics = data.get("topics", [])

    grounding_lines = index_lines + news_lines + stock_candidate_lines
    grounding_text = "\n".join(grounding_lines)
    grounding_numbers = _number_tokens(grounding_text)

    verified, rejected = [], []
    for topic in raw_topics:
        failure_reason = _verify_topic(topic, grounding_text, grounding_numbers)
        if failure_reason is None:
            verified.append(topic)
        else:
            rejected.append({
                "team": topic.get("team", "?"),
                "name": topic.get("name", "?"),
                "reason": failure_reason,
            })

    return verified, rejected
