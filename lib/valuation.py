"""객관적 데이터 기반 적정주가 산출 — stock_analyzer.py 전용.

성장률 전망치처럼 주관적 가정이 필요한 방법(DCF, 배당할인모형)은 넣지 않는다.
공시된 재무 데이터(EPS·BPS·동일업종 PER)와 검증된 계산식만으로 산출 가능한
2가지 방법만 쓴다 — 가이드 4-4 원칙(확인 안 되면 만들지 않는다)과 같은 맥락으로,
근거 없는 가정을 숫자로 만들어내지 않는다.

1. PER 밸류에이션: EPS × 동일업종 PER — 같은 업종에 시장이 실제로 매기는 배수를
   그대로 적용한다.
2. 그레이엄 공식(Graham Number): sqrt(22.5 × EPS × BPS) — 벤저민 그레이엄이 제시한
   보수적 적정주가 상한선(PER 15배 × PBR 1.5배 = 22.5 가정), 업종 비교 데이터가
   없어도 계산 가능하다.

두 방법의 평균을 "적정주가 추정치"로 삼아 현재가와의 괴리율로 저평가/고평가/적정
구간을 판정한다. 필요한 데이터가 없으면 해당 방법은 건너뛰고, 둘 다 산출 불가면
"판단 불가"로 명시한다.
"""

import math
from dataclasses import dataclass, field

GAP_UNDERVALUED_PCT = 20.0
GAP_OVERVALUED_PCT = -20.0


@dataclass
class FairValueEstimate:
    method: str
    fair_price: float
    basis: str


@dataclass
class ValuationResult:
    current_price: float | None = None
    estimates: list[FairValueEstimate] = field(default_factory=list)

    @property
    def average_fair_price(self) -> float | None:
        if not self.estimates:
            return None
        return sum(e.fair_price for e in self.estimates) / len(self.estimates)

    @property
    def gap_pct(self) -> float | None:
        avg = self.average_fair_price
        if avg is None or not self.current_price:
            return None
        return (avg - self.current_price) / self.current_price * 100

    @property
    def verdict(self) -> str:
        gap = self.gap_pct
        if gap is None:
            return "판단 불가 (산출 가능한 밸류에이션 방법 없음)"
        if gap >= GAP_UNDERVALUED_PCT:
            return "저평가"
        if gap <= GAP_OVERVALUED_PCT:
            return "고평가"
        return "적정 구간"


def per_valuation(eps: float | None, industry_per: float | None) -> FairValueEstimate | None:
    if eps is None or industry_per is None or eps <= 0 or industry_per <= 0:
        return None
    fair_price = eps * industry_per
    return FairValueEstimate(
        method="PER 밸류에이션",
        fair_price=fair_price,
        basis=f"EPS {eps:,.0f}원 × 동일업종 PER {industry_per:.2f}배",
    )


def graham_number(eps: float | None, bps: float | None) -> FairValueEstimate | None:
    if eps is None or bps is None or eps <= 0 or bps <= 0:
        return None
    fair_price = math.sqrt(22.5 * eps * bps)
    return FairValueEstimate(
        method="그레이엄 공식",
        fair_price=fair_price,
        basis=f"sqrt(22.5 × EPS {eps:,.0f}원 × BPS {bps:,.0f}원)",
    )


def evaluate(
    current_price: float | None,
    eps: float | None,
    bps: float | None,
    industry_per: float | None,
) -> ValuationResult:
    result = ValuationResult(current_price=current_price)
    for estimate in (per_valuation(eps, industry_per), graham_number(eps, bps)):
        if estimate is not None:
            result.estimates.append(estimate)
    return result
