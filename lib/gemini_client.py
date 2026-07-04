"""Gemini API로 팀별 주제 후보 3건(종목리포트/마켓칼럼/IPO 각 1건) + 상세 브리핑 생성.

가이드 4-4 원칙: 수치가 확인되지 않은 주제는 생성하지 않는다(추측 금지).
확인되지 않으면 해당 팀 주제를 아예 만들지 않아도 된다(3건 미만 가능).
"""

import json
import os

from google import genai
from google.genai import types

MODEL_NAME = "gemini-2.0-flash"

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
"""


def _build_prompt(run_note: str | None, index_lines: list[str], news_lines: list[str]) -> str:
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

    return (
        "너는 증권 매체의 데스크다. 아래 확인된 지수/뉴스 데이터만 근거로 삼아 "
        "'종목리포트', '마켓칼럼', 'IPO' 세 팀에 각각 1건씩 기사 주제 후보를 만들어라.\n\n"
        + "\n".join(context_lines)
        + "\n\n"
        + _RESPONSE_SCHEMA_HINT
    )


def generate_topics(run_note: str | None, index_lines: list[str], news_lines: list[str]) -> list[dict]:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = _build_prompt(run_note, index_lines, news_lines)
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    data = json.loads(response.text)
    return data.get("topics", [])
