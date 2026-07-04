"""완료 주제 필터링. completed_topics.json은 사람이 수동으로 갱신하므로 여기서는 읽기만 한다."""

import json
from pathlib import Path

COMPLETED_TOPICS_PATH = Path(__file__).resolve().parent.parent / "completed_topics.json"


def load_completed_names() -> set[str]:
    with open(COMPLETED_TOPICS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {topic["name"] for topic in data.get("topics", [])}


def filter_new_topics(topics: list[dict]) -> tuple[list[dict], list[dict]]:
    """(신규 후보, 제외된 후보) 반환. 이름이 완전히 일치하는 경우만 제외한다."""
    completed = load_completed_names()
    kept, excluded = [], []
    for topic in topics:
        if topic.get("name") in completed:
            excluded.append(topic)
        else:
            kept.append(topic)
    return kept, excluded
