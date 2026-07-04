"""날짜/요일/휴장일 판단.

wp-invest-article-builder SKILL.md Step 3.5 규칙을 반영한다:
- 주말·공휴일 실행 시 "오늘 마감/청약/발표" 류 표현 금지, 직전 거래일 데이터임을 명시
- 평일 장마감 후 실행 시 당일 마감 수치 기준으로 진행
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pykrx import stock

KST = ZoneInfo("Asia/Seoul")

# pykrx 지수 코드: 1001 = 코스피
_KOSPI_INDEX_CODE = "1001"


@dataclass
class RunContext:
    now_kst: datetime
    mode: str  # "weekday" | "weekend" | "holiday"
    data_date: date  # 기준으로 삼을 거래일 (지수/가격 조회용)
    note: str | None  # 리포트 상단에 표시할 안내 문구


def is_trading_day(day: date) -> bool:
    """pykrx에 해당일 코스피 지수 데이터가 있으면 거래일로 판단한다."""
    date_str = day.strftime("%Y%m%d")
    try:
        df = stock.get_index_ohlcv_by_date(date_str, date_str, _KOSPI_INDEX_CODE)
    except Exception:
        # pykrx 조회 자체가 실패하면 주말 여부만으로 판단(보수적으로 평일은 거래일 취급)
        return day.weekday() < 5
    return not df.empty


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
