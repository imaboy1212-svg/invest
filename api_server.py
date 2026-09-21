"""bestwellth.org 위젯용 종목분석 API — 종목명/코드를 받아 적정주가 판단
결과를 JSON으로 반환한다.

PythonAnywhere Web App으로 배포하는 것을 전제로 만들었다(README "종목분석기
위젯 API" 절 참고). 실제 데이터 수집은 lib/analysis.py(stock_analyzer.py와
공유)가 담당하고, 이 파일은 그 결과를 JSON으로 직렬화하는 얇은 웹 레이어다.

위젯은 응답 속도가 중요해서 DART 실적/공시·뉴스 조회는 건너뛴다
(include_reference_info=False) — 그 정보는 어차피 밸류에이션 판정에 반영되지
않는 참고용이고, DART corpCode.xml 최초 다운로드 등으로 응답이 느려질 수 있어
웹 요청 경로에서는 뺐다. 필요하면 CLI(stock_analyzer.py)를 쓴다.
"""

from flask import Flask, jsonify, request

from lib import analysis
from lib.analysis import PriceFetchError, StockNotFoundError

app = Flask(__name__)

# bestwellth.org에서만 이 API를 호출할 수 있도록 제한한다. 다른 오리진에서 오는
# 요청은 CORS 헤더를 안 붙여서 브라우저가 응답을 막게 한다(서버가 데이터 자체를
# 막지는 못하지만, 이 API는 공개 시세 정보라 그 이상의 접근 제어는 불필요하다고
# 판단 — 필요하면 여기에 오리진을 추가/제거).
_ALLOWED_ORIGINS = {
    "https://bestwellth.org",
    "https://www.bestwellth.org",
}


@app.after_request
def _add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    if origin in _ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return response


def _serialize(result: analysis.AnalysisResult) -> dict:
    fund = result.fundamentals
    val = result.valuation
    price_range = result.price_range

    return {
        "name": result.name,
        "code": result.code,
        "current_price": price_range.current,
        "high_52w": price_range.high_52w,
        "low_52w": price_range.low_52w,
        "pct_below_52w_high": round(price_range.pct_below_52w_high, 1),
        "fundamentals": (
            {
                "per": fund.per,
                "eps": fund.eps,
                "pbr": fund.pbr,
                "bps": fund.bps,
                "industry_per": fund.industry_per,
                "dividend_yield_pct": fund.dividend_yield_pct,
            }
            if fund is not None
            else None
        ),
        "valuation": {
            "estimates": [
                {"method": e.method, "fair_price": round(e.fair_price), "basis": e.basis}
                for e in val.estimates
            ],
            "average_fair_price": (
                round(val.average_fair_price) if val.average_fair_price is not None else None
            ),
            "gap_pct": round(val.gap_pct, 1) if val.gap_pct is not None else None,
            "verdict": val.verdict,
        },
        "technical_signals": [] if result.technical.error else [s.text for s in result.technical.signals],
    }


@app.route("/api/analyze")
def analyze():
    query = request.args.get("stock", "").strip()
    if not query:
        return jsonify({"error": "stock 파라미터가 필요합니다 (예: ?stock=삼성전자)"}), 400

    try:
        result = analysis.run_analysis(query, include_reference_info=False)
    except StockNotFoundError:
        return jsonify({"error": f"종목을 찾을 수 없습니다: {query}"}), 404
    except PriceFetchError:
        return jsonify({"error": "현재가 조회에 실패했습니다. 잠시 후 다시 시도해 주세요."}), 502

    return jsonify(_serialize(result))


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True)
