"""최근 종목리포트에서 다룬 종목 기록 — 같은 종목이 계속 반복 선정되는 것을 막는다.

completed_topics.json은 사람이 수동으로 갱신하는 "완료 주제" 목록이라 매일
자동으로 갱신되지 않는다. 반면 이 파일은 스크립트가 실행할 때마다 자동으로
기록/정리해서, 네이버 인기종목에 거의 항상 걸리는 대형주(예: SK하이닉스)가
매번 종목리포트로 뽑히는 것을 쿨다운 기간 동안 막는다.
"""

import json
from datetime import date, timedelta
from pathlib import Path

RECENT_PICKS_PATH = Path(__file__).resolve().parent.parent / "recent_stock_picks.json"
COOLDOWN_DAYS = 5


def _load() -> list[dict]:
    if not RECENT_PICKS_PATH.exists():
        return []
    try:
        with open(RECENT_PICKS_PATH, encoding="utf-8") as f:
            return json.load(f).get("picks", [])
    except Exception:
        return []


def get_recent_names(today: date) -> set[str]:
    """쿨다운 기간(COOLDOWN_DAYS) 안에 이미 다룬 종목명 집합."""
    cutoff = today - timedelta(days=COOLDOWN_DAYS)
    names = set()
    for pick in _load():
        try:
            picked_date = date.fromisoformat(pick["date"])
        except (KeyError, ValueError):
            continue
        if picked_date >= cutoff:
            names.add(pick["name"])
    return names


def record_pick(name: str, today: date) -> None:
    """오늘 선정된 종목을 기록하고, 쿨다운 지난 옛날 기록은 정리한다."""
    cutoff = today - timedelta(days=COOLDOWN_DAYS)
    picks = [p for p in _load() if _safe_date(p) and _safe_date(p) >= cutoff]
    picks.append({"name": name, "date": today.isoformat()})
    with open(RECENT_PICKS_PATH, "w", encoding="utf-8") as f:
        json.dump({"picks": picks}, f, ensure_ascii=False, indent=2)


def _safe_date(pick: dict) -> date | None:
    try:
        return date.fromisoformat(pick["date"])
    except (KeyError, ValueError):
        return None
