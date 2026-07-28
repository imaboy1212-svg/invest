"""보조지표 계산 (이동평균/골든·데드크로스, RSI, MACD, 거래량 급증, 볼린저밴드).

가격 시계열은 Yahoo Finance(yfinance)에서 받는다 — 이미 lib/market_data.py가 같은
방식으로 종목 시세 교차검증에 쓰고 있어 이 실행 환경에서 동작이 검증된 소스다.
지표는 전부 pandas로 직접 계산해서(외부 TA 라이브러리 의존 없음) 값 하나하나를
signals 문자열로 남기고, 그 문자열만 리포트에 쓴다 — LLM이 숫자를 지어내지
않도록 이 모듈에서 계산한 사실만 그대로 노출하는 것이 목적이다.
"""

from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf

_MA_WINDOWS = (5, 20, 60, 120)
_RSI_WINDOW = 14
_BB_WINDOW = 20
_BB_STD_MULT = 2
_VOLUME_AVG_WINDOW = 20
_VOLUME_SURGE_RATIO = 2.0  # 20일 평균 대비 이 배수 이상이면 "급증"으로 표시
_CROSS_LOOKBACK_DAYS = 3  # 최근 며칠 안의 크로스만 "발생"으로 표시


@dataclass
class TechnicalSnapshot:
    code: str
    close: float | None = None
    ma: dict[int, float] = field(default_factory=dict)
    rsi14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    volume_ratio_20d: float | None = None
    signals: list[str] = field(default_factory=list)
    error: str | None = None


def _fetch_history(code: str) -> pd.DataFrame | None:
    for suffix in ("KS", "KQ"):
        try:
            hist = yf.Ticker(f"{code}.{suffix}").history(period="1y")
        except Exception:
            continue
        if not hist.empty and len(hist) >= 30:
            return hist
    return None


def _rsi(close: pd.Series, window: int = _RSI_WINDOW) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window).mean()
    avg_loss = loss.rolling(window).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return macd_line, signal_line


def _detect_cross(fast: pd.Series, slow: pd.Series, lookback: int = _CROSS_LOOKBACK_DAYS) -> str | None:
    """최근 lookback 거래일 안에 fast가 slow를 상향/하향 돌파했는지."""
    diff = (fast - slow).dropna()
    if len(diff) < lookback + 1:
        return None
    recent = diff.iloc[-(lookback + 1):]
    for i in range(1, len(recent)):
        prev, curr = recent.iloc[i - 1], recent.iloc[i]
        if prev <= 0 < curr:
            return "golden"
        if prev >= 0 > curr:
            return "dead"
    return None


def analyze(code: str) -> TechnicalSnapshot:
    snapshot = TechnicalSnapshot(code=code)
    hist = _fetch_history(code)
    if hist is None:
        snapshot.error = "가격 시계열 조회 실패 (Yahoo Finance)"
        return snapshot

    close = hist["Close"]
    volume = hist["Volume"]
    snapshot.close = float(close.iloc[-1])

    for window in _MA_WINDOWS:
        if len(close) >= window:
            snapshot.ma[window] = float(close.rolling(window).mean().iloc[-1])

    if 5 in snapshot.ma and 20 in snapshot.ma:
        cross = _detect_cross(close.rolling(5).mean(), close.rolling(20).mean())
        if cross == "golden":
            snapshot.signals.append("5일선이 20일선을 상향 돌파(골든크로스, 최근 3거래일 내)")
        elif cross == "dead":
            snapshot.signals.append("5일선이 20일선을 하향 돌파(데드크로스, 최근 3거래일 내)")

    if 20 in snapshot.ma and 60 in snapshot.ma:
        cross = _detect_cross(close.rolling(20).mean(), close.rolling(60).mean())
        if cross == "golden":
            snapshot.signals.append("20일선이 60일선을 상향 돌파(중기 골든크로스, 최근 3거래일 내)")
        elif cross == "dead":
            snapshot.signals.append("20일선이 60일선을 하향 돌파(중기 데드크로스, 최근 3거래일 내)")

    rsi_series = _rsi(close)
    if not rsi_series.dropna().empty:
        snapshot.rsi14 = float(rsi_series.iloc[-1])
        if snapshot.rsi14 <= 30:
            snapshot.signals.append(f"RSI(14) {snapshot.rsi14:.1f} — 과매도권(30 이하)")
        elif snapshot.rsi14 >= 70:
            snapshot.signals.append(f"RSI(14) {snapshot.rsi14:.1f} — 과매수권(70 이상)")

    macd_line, signal_line = _macd(close)
    if not macd_line.dropna().empty:
        snapshot.macd = float(macd_line.iloc[-1])
        snapshot.macd_signal = float(signal_line.iloc[-1])
        cross = _detect_cross(macd_line, signal_line)
        if cross == "golden":
            snapshot.signals.append("MACD가 시그널선을 상향 돌파(골든크로스, 최근 3거래일 내)")
        elif cross == "dead":
            snapshot.signals.append("MACD가 시그널선을 하향 돌파(데드크로스, 최근 3거래일 내)")

    if len(close) >= _BB_WINDOW:
        mid = close.rolling(_BB_WINDOW).mean().iloc[-1]
        std = close.rolling(_BB_WINDOW).std().iloc[-1]
        snapshot.bb_upper = float(mid + _BB_STD_MULT * std)
        snapshot.bb_lower = float(mid - _BB_STD_MULT * std)
        if snapshot.close is not None:
            if snapshot.close < snapshot.bb_lower:
                snapshot.signals.append("볼린저밴드 하단 이탈(20일, 2표준편차)")
            elif snapshot.close > snapshot.bb_upper:
                snapshot.signals.append("볼린저밴드 상단 이탈(20일, 2표준편차)")

    if len(volume) >= _VOLUME_AVG_WINDOW + 1:
        avg_volume = volume.iloc[-(_VOLUME_AVG_WINDOW + 1):-1].mean()
        today_volume = volume.iloc[-1]
        if avg_volume > 0:
            snapshot.volume_ratio_20d = float(today_volume / avg_volume)
            if snapshot.volume_ratio_20d >= _VOLUME_SURGE_RATIO:
                snapshot.signals.append(
                    f"거래량 20일 평균 대비 {snapshot.volume_ratio_20d * 100:.0f}% (급증)"
                )

    if not snapshot.signals:
        snapshot.signals.append("특이 신호 없음 (평이한 구간)")

    return snapshot
