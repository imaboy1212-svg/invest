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
        "intro_angle": "서론 각도 2~3문장",
        "body_points": ["본론에서 다룰 관점 1", "본론에서 다룰 관점 2", "..."]
      }
    }
  ]
}
분량 기준 (기존 대비 2배 이상으로 훨씬 풍부하게 채울 것):
- key_figures는 6~10개
- related_news는 4~6개
- article_structure.body_points는 4~6개, 각 항목은 구체적인 분석 관점을 2~3문장으로
- reason(추천 사유)도 1줄이 아니라 2~3문장으로 왜 지금 이 주제가 중요한지 상세히 서술
확인된 수치·뉴스가 부족해 특정 팀(종목리포트/마켓칼럼/IPO) 주제를 만들 수 없으면
그 팀은 topics 배열에서 아예 제외하라. 분량을 채우려고 존재하지 않는 수치나 뉴스를
지어내는 것보다는, 확인 가능한 범위 안에서 최대한 깊이 있게 다루는 쪽을 우선하라.

중요 — 팀별 key_figures/내용 구분 규칙:
- '종목리포트' team은 지수(코스피/코스닥) 얘기를 전혀 하지 마라. key_figures는 물론이고
  intro_angle, body_points, reason 어디에도 코스피/코스닥 언급을 넣지 마라. 시장 전체
  상황을 배경으로 쓰지 말고, 오직 그 종목 자체(등락률, 거래대금, 수급, 시가총액, PER/PBR,
  실적, 최근 뉴스·공시, 사업 배경 등)에만 집중해 심층적으로 써라.
- '종목리포트' team의 name(주제명)에는 반드시 특정 상장 기업 1개의 정확한 종목명을
  포함해야 한다. "소부장", "2차전지 관련주", "반도체 테마", "AI 관련주"처럼 섹터·테마·
  업종명이나 "~관련주"/"~업계"/"~업종" 형태, 또는 여러 기업을 묶어 다루는 주제는
  절대 '종목리포트'로 만들지 마라. 그런 섹터·테마성 소재는 '마켓칼럼'으로 분류하라.
  종목리포트인데 특정 기업 1개로 좁혀지지 않으면 차라리 그 팀을 비워라.
- 코스피/코스닥 지수, 해외 지수(S&P500/나스닥/다우), 환율, 유가 등 시장 전체·국제 정세
  데이터는 '마켓칼럼' team에서만 사용하라. 마켓칼럼은 국내 지수뿐 아니라 아래 제공된
  [해외 지수·환율·유가 현황]과 국제 정세 관련 뉴스가 있으면 적극 반영해서 국내외 시장
  흐름을 종합적으로 분석하라.

절대 규칙 — 원문 밖 내용 생성 금지 (환각 금지):
- 제공된 뉴스 목록의 각 줄은 "[언론사] 제목 - 요약" 형식이다. related_news를 채울 때
  "언론사" 부분은 source 필드에, "제목" 부분만(대괄호 없이, " - 요약" 부분도 제외하고)
  headline 필드에 글자 단위로 동일하게 옮겨 적어라. headline에 대괄호나 언론사명을
  다시 넣지 마라 (source 필드와 중복 표기됨). 문장을 다듬거나 요약하거나 새로 만들지
  마라. 뒤에 붙은 "- 요약" 내용은 key_figures나 intro_angle/body_points를 더 풍부하고
  구체적으로 쓰는 데 참고 자료로만 사용하고, 숫자·회사명은 아래 규칙을 반드시 지켜라.
- key_figures, name, reason, intro_angle, body_points에 들어가는 회사명·금액·비율·
  계약규모 등 모든 숫자와 고유명사는 반드시 아래 제공된 원문(지수/뉴스/종목후보/IPO일정/
  해외지수)에 등장하는 것만 사용하라. 원문에 없는 회사명, 금액, 수치는 절대로 만들어내지
  마라 (예: 헤드라인에 언급되지 않은 계약 상대방 회사명을 지어내는 것 금지).
- 원문만으로 특정 수치나 이름을 확인할 수 없으면, 그 항목은 비워두거나 "확인 필요"라고
  적어라. 분량을 채우기 위해 그럴듯하게 추정해서 채우지 마라.
"""


def _build_prompt(
    run_note: str | None,
    index_lines: list[str],
    news_lines: list[str],
    stock_candidate_lines: list[str],
    ipo_lines: list[str],
    global_lines: list[str],
    avoid_stock_names: set[str],
) -> str:
    context_lines = []
    if run_note:
        context_lines.append(f"[실행 기준] {run_note}")
    context_lines.append("[지수 현황]")
    context_lines.extend(index_lines)
    if global_lines:
        context_lines.append("[해외 지수·환율·유가 현황 - 마켓칼럼 전용]")
        context_lines.extend(global_lines)
    context_lines.append("[증권 뉴스 헤드라인]")
    if news_lines:
        context_lines.extend(news_lines)
    else:
        context_lines.append("(뉴스 수집 실패 - 지수 데이터만 활용)")
    if stock_candidate_lines:
        context_lines.append("[종목리포트 후보 종목 - 네이버증권 인기종목 중 뉴스·공시 확인된 종목]")
        context_lines.extend(stock_candidate_lines)
        context_lines.append(
            "종목리포트 주제는 가능하면 위 후보 종목 중에서 고르되, 확인된 뉴스나 수치 근거가 "
            "있는 종목을 우선하라. 후보에 없어도 뉴스에 확실한 근거가 있는 종목이면 사용해도 된다. "
            "후보 종목이라도 관련 기사·공시가 실제로 없으면 억지로 종목리포트를 만들지 말고 "
            "그 팀은 비워라."
        )
    if avoid_stock_names:
        context_lines.append(
            "[종목리포트 반복 방지] 아래 종목은 최근 며칠 안에 이미 종목리포트로 다뤘으니, "
            "다른 확실한 근거가 있는 종목이 있다면 이번엔 피하라 (매번 같은 종목만 반복되는 "
            "것을 막기 위함): " + ", ".join(sorted(avoid_stock_names))
        )
    if ipo_lines:
        context_lines.append("[공모주 청약·상장 일정 - 네이버증권]")
        context_lines.extend(ipo_lines)
        context_lines.append(
            "IPO 주제는 위 공모주 일정 중 앞으로 다가올 청약·상장 일정이 있는 종목을 골라 작성하라. "
            "일정표에 없는 종목명·날짜·공모가는 만들지 마라."
        )
    else:
        context_lines.append("[공모주 청약·상장 일정] 조회된 일정 없음 - IPO 주제는 만들지 마라.")

    return (
        "너는 증권 매체의 데스크다. 아래 확인된 지수/뉴스 데이터만 근거로 삼아 "
        "'종목리포트', '마켓칼럼', 'IPO' 세 팀에 각각 1건씩 기사 주제 후보를 만들어라.\n\n"
        + "\n".join(context_lines)
        + "\n\n"
        + _RESPONSE_SCHEMA_HINT
    )


_NUMBER_RE = re.compile(r"\d[\d,]*")

_SECTOR_THEME_KEYWORDS = (
    "관련주", "테마주", "테마", "업종", "업계", "밸류체인", "섹터", "소부장",
)


def _number_tokens(text: str) -> set[str]:
    return {tok.replace(",", "") for tok in _NUMBER_RE.findall(text) if len(tok.replace(",", "")) >= 2}


def _verify_topic(
    topic: dict, grounding_text: str, grounding_numbers: set[str], avoid_stock_names: set[str]
) -> str | None:
    """원문 밖 회사명·숫자 생성(환각) 여부를 단순 포함 검사로 확인한다.

    문제없으면 None, 문제가 있으면 실패 사유 문자열을 반환한다.
    """
    for news in topic.get("related_news", []):
        headline = news.get("headline", "")
        if headline and headline not in grounding_text:
            return f"관련 뉴스 헤드라인이 원문에 없음: {headline!r}"

    text_fields = [kf.get("figure", "") for kf in topic.get("key_figures", [])]
    text_fields.append(topic.get("reason", ""))
    structure = topic.get("article_structure", {})
    text_fields.append(structure.get("intro_angle", ""))
    text_fields.extend(structure.get("body_points", []))

    for field_text in text_fields:
        for number in _number_tokens(field_text):
            if number not in grounding_numbers:
                return f"수치가 원문에 없음: {number!r} (내용: {field_text!r})"

    # 종목리포트의 지수(코스피/코스닥) 언급은 여기서 거부하지 않는다 — 검증 실패로 전체
    # 주제를 버리기보다, topic_recommender._enforce_stock_report_figures가 해당 부분만
    # 제거하고 나머지 심층 내용은 살리는 쪽이 분량/품질에 유리하다.

    if topic.get("team") == "종목리포트":
        name = topic.get("name", "")
        for keyword in _SECTOR_THEME_KEYWORDS:
            if keyword in name:
                return f"종목리포트가 섹터/테마를 다룸(단일 종목 아님) — {keyword!r} 감지: {name!r}"
        for avoided in avoid_stock_names:
            if avoided in name:
                return f"쿨다운 중인 종목 반복 선정: {avoided!r} (프롬프트 지시를 따르지 않음)"

    return None


_TOPIC_ITEM_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "team": {"type": "STRING"},
        "name": {"type": "STRING"},
        "golden_time": {"type": "BOOLEAN"},
        "reason": {"type": "STRING"},
        "key_figures": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"figure": {"type": "STRING"}, "source": {"type": "STRING"}},
                "required": ["figure", "source"],
            },
        },
        "related_news": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"headline": {"type": "STRING"}, "source": {"type": "STRING"}},
                "required": ["headline", "source"],
            },
        },
        "article_structure": {
            "type": "OBJECT",
            "properties": {
                "intro_angle": {"type": "STRING"},
                "body_points": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
            "required": ["intro_angle", "body_points"],
        },
    },
    "required": ["team", "name", "golden_time", "reason", "key_figures", "related_news", "article_structure"],
}

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"topics": {"type": "ARRAY", "items": _TOPIC_ITEM_SCHEMA}},
    "required": ["topics"],
}


def generate_topics(
    run_note: str | None,
    index_lines: list[str],
    news_lines: list[str],
    stock_candidate_lines: list[str] | None = None,
    ipo_lines: list[str] | None = None,
    global_lines: list[str] | None = None,
    avoid_stock_names: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """(검증 통과한 주제 목록, 검증 실패로 제외된 주제 목록[team/name/reason]) 반환.

    Gemini 응답이 길어질수록(분량 확대 지시) JSON이 깨질 위험이 커져서,
    response_schema로 구조를 강제(controlled generation)한다. 그래도 파싱이
    실패하면 파이프라인을 죽이지 않고 이번 회차 주제 0건으로 처리한다.
    """
    stock_candidate_lines = stock_candidate_lines or []
    ipo_lines = ipo_lines or []
    global_lines = global_lines or []
    avoid_stock_names = avoid_stock_names or set()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = _build_prompt(
        run_note, index_lines, news_lines, stock_candidate_lines, ipo_lines, global_lines, avoid_stock_names
    )
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
        ),
    )

    try:
        data = json.loads(response.text)
        raw_topics = data.get("topics", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        print(f"[Gemini응답파싱실패] {type(exc).__name__}: {exc} / 응답 앞부분: {str(response.text)[:300]!r}")
        raw_topics = []

    grounding_lines = index_lines + news_lines + stock_candidate_lines + ipo_lines + global_lines
    grounding_text = "\n".join(grounding_lines)
    grounding_numbers = _number_tokens(grounding_text)

    verified, rejected = [], []
    for topic in raw_topics:
        failure_reason = _verify_topic(topic, grounding_text, grounding_numbers, avoid_stock_names)
        if failure_reason is None:
            verified.append(topic)
        else:
            rejected.append({
                "team": topic.get("team", "?"),
                "name": topic.get("name", "?"),
                "reason": failure_reason,
            })

    return verified, rejected
