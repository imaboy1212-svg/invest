"""날짜/요일/휴장일 판단.

wp-invest-article-builder SKILL.md Step 3.5 규칙을 반영한다:
- 주말·공휴일 실행 시 "오늘 마감/청약/발표" 류 표현 금지, 직전 거래일 데이터임을 명시
- 평일 장마감 후 실행 시 당일 마감 수치 기준으로 진행

공휴일 판단은 이전에 pykrx(get_index_ohlcv_by_date)로 "해당일 지수 데이터가 있는지"를
확인하는 방식이었으나, 이 실행 환경에서 KRX가 pykrx 직접호출을 항상 차단해 매번 실패하고
"평일이면 거래일"로만 판단하는 상태였다 (2026-07-20 확인, 실질적으로 공휴일 판단이 한 번도
동작한 적이 없었음). 네트워크 호출이 필요 없는 holidays 라이브러리(한국 공휴일)로 교체.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays

KST = ZoneInfo("Asia/Seoul")

_KR_HOLIDAYS = holidays.KR()


@dataclass
class RunContext:
    now_kst: datetime
    mode: str  # "weekday" | "weekend" | "holiday"
    data_date: date  # 기준으로 삼을 거래일 (지수/가격 조회용)
    note: str | None  # 리포트 상단에 표시할 안내 문구


def is_trading_day(day: date) -> bool:
    """평일이면서 한국 공휴일이 아니면 거래일로 판단한다."""
    return day.weekday() < 5 and day not in _KR_HOLIDAYS


def last_trading_day(day: date) -> date:
    """day 이전(day 포함하지 않음) 가장 최근 거래일을 찾는다."""
    cursor = day - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    raise RuntimeError(f"{day} 기준 최근 10일 내 거래일을 찾지 못했습니다")


def get_run_context(now_kst: datetime | None = None) -> RunContext:
    now = now_kst or datetime.now(KST)
    today = now.date()
    is_weekend = today.weekday() >= 5  # 5=토, 6=일

    if not is_weekend and is_trading_day(today):
        return RunContext(now_kst=now, mode="weekday", data_date=today, note=None)

    data_date = last_trading_day(today)
    if is_weekend:
        note = f"주말 기준, {data_date.isoformat()} 종가 데이터 (금요일 종가 기준)"
        mode = "weekend"
    else:
        note = f"휴일 기준, 직전 거래일({data_date.isoformat()}) 데이터"
        mode = "holiday"
    return RunContext(now_kst=now, mode=mode, data_date=data_date, note=note)
