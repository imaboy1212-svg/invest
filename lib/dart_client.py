"""DART(전자공시시스템) Open API — 종목코드→고유번호 매핑, 최근 공시, 최근 실적(YoY).

corpCode.xml은 상장사 전체 목록(수 MB짜리 zip)이라 매 실행마다 새로 받지 않고
로컬에 캐시(dart_corp_codes.json)해 둔다. 하루 한 번 도는 배치라 캐시가
하루 이상 지났을 때만 갱신한다.

가이드 4-4 원칙(확인 안 되면 만들지 않는다)과 동일하게, 계정명이 예상과 다르거나
API가 실패하면 조용히 넘어가지 않고 None/빈 리스트 + 로그를 남겨서 호출부가
"실적 확인 실패"로 명확히 표시할 수 있게 한다.
"""

import io
import json
import os
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import requests

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_BASE_URL = "https://opendart.fss.or.kr/api"
_CORP_CODE_CACHE_PATH = Path(__file__).resolve().parent.parent / "dart_corp_codes.json"
_CORP_CODE_CACHE_MAX_AGE_DAYS = 3

# 연도 진행에 따라 최근 실적일 확률이 높은 보고서부터 시도한다 (연간→분기 순서가 아니라
# "지금 시점에 이미 공시됐을 가능성이 높은 것부터"). 실패하면 다음 후보로 넘어간다.
_REPRT_CODES = {
    "1분기보고서": "11013",
    "반기보고서": "11012",
    "3분기보고서": "11014",
    "사업보고서": "11011",
}


@dataclass
class EarningsSnapshot:
    period_label: str
    revenue: int | None = None
    revenue_yoy_pct: float | None = None
    operating_profit: int | None = None
    operating_profit_yoy_pct: float | None = None
    operating_profit_turned_profitable: bool = False
    net_income: int | None = None
    net_income_yoy_pct: float | None = None

    @property
    def is_improving(self) -> bool:
        """영업이익이 개선됐다고 볼 근거가 있는지 (증가율 양수 또는 흑자전환)."""
        if self.operating_profit_turned_profitable:
            return True
        return self.operating_profit_yoy_pct is not None and self.operating_profit_yoy_pct > 0

    def summary_line(self) -> str:
        parts = [f"{self.period_label} 실적"]
        if self.revenue is not None:
            yoy = f"({self.revenue_yoy_pct:+.1f}%)" if self.revenue_yoy_pct is not None else ""
            parts.append(f"매출액 {self.revenue:,}백만원{yoy}")
        if self.operating_profit is not None:
            if self.operating_profit_turned_profitable:
                yoy = "(흑자전환)"
            elif self.operating_profit_yoy_pct is not None:
                yoy = f"({self.operating_profit_yoy_pct:+.1f}%)"
            else:
                yoy = ""
            parts.append(f"영업이익 {self.operating_profit:,}백만원{yoy}")
        if self.net_income is not None:
            yoy = f"({self.net_income_yoy_pct:+.1f}%)" if self.net_income_yoy_pct is not None else ""
            parts.append(f"순이익 {self.net_income:,}백만원{yoy}")
        return " / ".join(parts)


@dataclass
class Disclosure:
    report_name: str
    receipt_date: str  # YYYYMMDD
    receipt_no: str

    @property
    def url(self) -> str:
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.receipt_no}"


def _api_key() -> str:
    return os.environ["DART_API_KEY"]


def _load_corp_code_cache() -> dict:
    if not _CORP_CODE_CACHE_PATH.exists():
        return {}
    try:
        with open(_CORP_CODE_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        cached_at = date.fromisoformat(data.get("cached_at", "1970-01-01"))
        if (date.today() - cached_at).days > _CORP_CODE_CACHE_MAX_AGE_DAYS:
            return {}
        return data.get("stock_code_to_corp_code", {})
    except Exception as exc:
        print(f"[DART] 고유번호 캐시 로드 실패: {type(exc).__name__}: {exc}")
        return {}


def _download_corp_codes() -> dict:
    """corpCode.xml(zip)을 받아 stock_code(6자리) -> corp_code(8자리) 매핑을 만들고 캐시한다.

    이 함수만 요청 자체에 try/except가 없어서(다른 DART 함수들과 다르게), DART
    서버 응답이 느리거나(타임아웃) 네트워크 문제가 있을 때 예외가 호출부까지
    전파돼 전체 분석(가격·재무지표·적정주가까지)이 통째로 죽는 버그가 있었다
    (2026-07-31 두산에너빌리티 실전 실행에서 opendart.fss.or.kr 연결 타임아웃으로
    확인). 다른 DART 함수들과 같은 방어 패턴으로 맞춘다 — 참고정보(실적/공시)
    조회 실패가 핵심 결과를 무너뜨리면 안 된다."""
    try:
        resp = requests.get(f"{_BASE_URL}/corpCode.xml", params={"crtfc_key": _api_key()}, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[DART] 고유번호 목록 다운로드 실패: {type(exc).__name__}: {exc}")
        return {}

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_bytes = zf.read(zf.namelist()[0])
    except zipfile.BadZipFile:
        # 키 오류 등으로 zip이 아니라 에러 JSON/XML이 그대로 온 경우
        print(f"[DART] corpCode.xml이 zip이 아님 (API 키 오류 가능성): {resp.content[:200]!r}")
        return {}

    root = ET.fromstring(xml_bytes)
    mapping = {}
    for item in root.findall("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code

    with open(_CORP_CODE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"cached_at": date.today().isoformat(), "stock_code_to_corp_code": mapping}, f, ensure_ascii=False)
    print(f"[DART] 고유번호 목록 갱신: {len(mapping)}개 상장사")
    return mapping


_corp_code_map: dict | None = None


def get_corp_code(stock_code: str) -> str | None:
    global _corp_code_map
    if _corp_code_map is None:
        _corp_code_map = _load_corp_code_cache() or _download_corp_codes()
    return _corp_code_map.get(stock_code)


def get_recent_disclosures(corp_code: str, days: int = 14, limit: int = 5) -> list[Disclosure]:
    end = date.today()
    begin = end - timedelta(days=days)
    try:
        resp = requests.get(
            f"{_BASE_URL}/list.json",
            params={
                "crtfc_key": _api_key(),
                "corp_code": corp_code,
                "bgn_de": begin.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "page_count": 20,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"[DART] 공시 목록 조회 실패(corp_code={corp_code}): {type(exc).__name__}: {exc}")
        return []

    if data.get("status") not in ("000", "013"):  # 013 = 조회된 데이터 없음(정상 상태)
        print(f"[DART] 공시 목록 API 오류(corp_code={corp_code}): {data.get('status')} {data.get('message')}")
        return []

    items = data.get("list", [])
    disclosures = [
        Disclosure(report_name=i["report_nm"], receipt_date=i["rcept_dt"], receipt_no=i["rcept_no"])
        for i in items
    ]
    return disclosures[:limit]


def _extract_account(items: list[dict], account_name: str, fs_div_priority: tuple[str, ...] = ("CFS", "OFS")) -> tuple[int | None, int | None]:
    """(당기금액, 전기금액)을 백만원 단위로 반환. 못 찾으면 (None, None)."""
    for fs_div in fs_div_priority:
        for item in items:
            if item.get("fs_div") != fs_div or item.get("sj_div") != "IS":
                continue
            if item.get("account_nm", "").strip() != account_name:
                continue
            try:
                thstrm = int(item["thstrm_amount"].replace(",", "")) // 1_000_000
                frmtrm_raw = item.get("frmtrm_amount", "").replace(",", "")
                frmtrm = int(frmtrm_raw) // 1_000_000 if frmtrm_raw not in ("", "-") else None
                return thstrm, frmtrm
            except (KeyError, ValueError):
                continue
    return None, None


def _yoy_pct(current: int | None, prior: int | None) -> float | None:
    """전기 대비 증가율(%). 전기가 0 이하(적자·손익없음)면 비율이 의미가 없어 None을
    반환한다 — 흑자전환 여부는 호출부에서 별도로 판단한다."""
    if current is None or prior is None or prior <= 0:
        return None
    return (current - prior) / prior * 100


def get_latest_earnings(corp_code: str) -> EarningsSnapshot | None:
    """최근 확정 실적을 최신 분기부터 역순으로 시도해 첫 성공을 반환한다.

    fnlttSinglAcntAll API는 당기/전기 금액을 한 번에 주기 때문에 별도로 전년도를
    다시 조회할 필요가 없다.
    """
    this_year = date.today().year
    candidates = [(this_year, name, code) for name, code in _REPRT_CODES.items()]
    candidates += [(this_year - 1, name, code) for name, code in _REPRT_CODES.items()]

    for year, report_label, reprt_code in candidates:
        try:
            resp = requests.get(
                f"{_BASE_URL}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": _api_key(),
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": reprt_code,
                    "fs_div": "CFS",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"[DART] 실적 조회 예외(corp_code={corp_code}, {year} {report_label}): {exc}")
            continue

        if data.get("status") != "000":
            continue  # 해당 연도/분기 보고서 미제출 — 다음 후보 시도

        items = data.get("list", [])
        revenue, revenue_prior = _extract_account(items, "매출액")
        op, op_prior = _extract_account(items, "영업이익")
        if op is None:
            op, op_prior = _extract_account(items, "영업이익(손실)")
        net, net_prior = _extract_account(items, "당기순이익")
        if net is None:
            net, net_prior = _extract_account(items, "당기순이익(손실)")

        if revenue is None and op is None and net is None:
            continue  # 계정명이 기대와 달라 아무 것도 못 찾음 — 다음 후보 시도

        turned_profitable = op is not None and op > 0 and op_prior is not None and op_prior <= 0

        return EarningsSnapshot(
            period_label=f"{year}년 {report_label}",
            revenue=revenue,
            revenue_yoy_pct=_yoy_pct(revenue, revenue_prior),
            operating_profit=op,
            operating_profit_yoy_pct=_yoy_pct(op, op_prior),
            operating_profit_turned_profitable=turned_profitable,
            net_income=net,
            net_income_yoy_pct=_yoy_pct(net, net_prior),
        )

    print(f"[DART] 실적 조회 전체 실패(corp_code={corp_code}) — 최근 4개 보고서 후보 모두 미확인")
    return None
