# WP투자정보본부 주제 후보 자동화 파이프라인

wp-invest-article-builder 스킬(종목리포트/마켓칼럼/IPO)의 **주제 추천 단계(Step 1)** 만 자동화하는 파이프라인이다.
매일 정해진 시각에 시장 데이터와 뉴스를 수집해 팀별 주제 후보 3건(종목리포트/마켓칼럼/IPO 각 1건)과 상세 브리핑을 생성하고,
GitHub에 커밋 + 텔레그램(@investwellth)으로 전송한다.

**이 파이프라인이 하지 않는 것**: 기사 본문 작성, 워드프레스 발행. 그 단계는 사람이 Claude.ai에서 wp-invest-article-builder 스킬로 직접 진행한다.

## 실행 흐름

1. 날짜/요일 검증 (평일 장마감 후 / 주말, 한국 공휴일 포함 — holidays 라이브러리로 판단)
2. Yahoo Finance + 네이버증권으로 국내 지수·종목 조회 및 교차검증 + 해외 지수(S&P500/나스닥/다우)·환율·유가 조회 (마켓칼럼용). pykrx(KRX 직접조회)는 이 실행 환경에서 KRX가 클라우드 IP를 차단해 항상 실패해서 완전히 제거함 (2026-07-20)
3. 증권 뉴스 크롤링 — 헤드라인뿐 아니라 기사 페이지의 og:description 메타태그로 본문 요약까지 수집 (네이버증권 주요뉴스 우선, 실패 시 매일경제로 폴백. 한국경제는 헤더를 강화해도 403이 지속돼 제외, 서울경제·파이낸셜뉴스는 기사 목록이 JS 렌더링이라 정적 크롤링이 불가능해 제외 — 2026-07-20 실행 로그로 확인. 소스는 필요에 따라 계속 추가 가능)
3-1. 종목리포트 후보 종목 발굴 (하드코딩 리스트 없음) — 네이버증권 코스피·코스닥 인기종목(실시간 인기검색)마다 개별 종목 뉴스·공시를 직접 조회해서, 실제 뉴스·공시가 있는 종목만 후보로 선정 (없으면 후보에서 제외, 억지로 만들지 않음). 최근 14일(약 2주) 안에 이미 다룬 종목은 `recent_stock_picks.json`으로 자동 기록해 후보에서 제외 (특정 대형주가 매번 반복 선정되는 것 방지, 2주 지나면 자동으로 다시 후보에 포함)
3-2. IPO 청약·상장 일정 조회 (네이버증권 공모주 페이지) — 실제 예정 일정 기반으로만 IPO 주제 작성
4. Gemini API로 팀별 주제 후보 3건 + 상세 브리핑 생성. 종목리포트는 반드시 특정 기업 1개(섹터·테마·"~관련주" 금지)에 대한 심층정보(등락률/네이버·Yahoo 교차검증 가격)만, 마켓칼럼은 국내외 지수·환율·유가·국제정세까지 종합 반영. 분량은 기존 대비 2배 이상(핵심 수치 6~10개, 관련 뉴스 4~6개, 본론 관점 4~6개, 리스크 요인 2~3개) — 사람이 이어서 기사를 쓸 때 바로 착수할 수 있을 만큼 재료를 채워서 생성. 종목 후보 목록이 있으면 종목리포트 생략 금지
5. `completed_topics.json` 기준으로 완료 주제 제외
6. 상세 브리핑 마크다운을 `briefings/`에 생성 및 커밋
7. 텔레그램으로 요약 메시지 + 브리핑 파일 전송

## 스케줄 (실제 운영: PythonAnywhere)

실제 운영은 PythonAnywhere 예약 작업(Tasks)이 담당한다. 실행 전 `git pull`로 이 저장소의
최신 코드를 받아온 뒤 실행하도록 구성되어 있어야 한다 (`cd .../invest && git pull &&
python3 topic_recommender.py`) — git pull 없이 실행하면 코드가 갱신되지 않는다.

- 평일: UTC 06:40 (KST 15:40, 장마감 후)
- 토·일: UTC 11:00 (KST 20:00, 금요일 데이터 기준 + 주말 뉴스 반영)

GitHub Actions 워크플로우(`.github/workflows/topic_recommender.yml`)는 같은 시각에 자동
실행되면 PythonAnywhere와 텔레그램이 중복 발송되고 브리핑 파일이 겹쳐 PythonAnywhere의
git pull이 충돌하는 문제가 있어, 자동 스케줄은 꺼두고 `workflow_dispatch`(수동 실행)로
코드 검증할 때만 사용한다.

## 로컬 실행

```bash
pip install -r requirements.txt
cp .env.example .env   # 키 채워넣기
python topic_recommender.py
```

## 시크릿

`GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`는 GitHub Actions Secrets에 등록한다. `.env`는 로컬 전용이며 커밋하지 않는다.

## 완료 주제 관리

`completed_topics.json`은 코로 님이 SKILL.md와 함께 수동으로 갱신한다 (자동 동기화 아님). 스크립트는 이 파일을 읽어 이름이 겹치는 후보만 제외한다.

## 최근 종목리포트 종목 관리

`recent_stock_picks.json`은 `completed_topics.json`과 달리 스크립트가 실행할 때마다 자동으로 기록·정리한다. 종목리포트로 다룬 종목명과 날짜를 저장해두고, 14일(약 2주) 이내에 이미 다룬 종목은 다음 발굴 후보에서 자동 제외한다. 2주가 지나면 자동으로 쿨다운이 풀려 다시 후보에 포함된다(중복 방지 목적일 뿐 영구 제외가 아니므로 이 정도 재선정은 허용). 수동으로 건드릴 필요 없음.

## 저평가·반등 스크리너 (`stock_screener.py`) — 별도 파이프라인

topic_recommender.py와 완전히 독립된 스크립트다. 코스피200·코스닥150 구성종목을
**점수제로 랭킹**해서 "상승 여력이 높아 보이는" 상위 `TOP_N`(기본 15)종목을
직전 분기 실적(참고용)·최근 공시(DART)·뉴스(네이버)·보조지표와 함께 텔레그램으로
전송한다.

### 설계 변경 이력 (2026-07-28, 사용자 피드백 기반)

- v1: "52주 고점 대비 -50%↓ + 실적 개선(YoY)"을 AND로 요구 → 거의 항상 0건
- v2: 하락폭 20~50% 구간 + 기술적 강세 신호 하나 이상(하드 필터) → 여전히 자주 0건
  (조건 하나만 안 맞아도 전체가 0건이 되는 구조적 약점)
- **v3(현재): 하드 필터를 없애고 점수제 랭킹으로 전환**
  - **실적은 점수에서 완전히 제외** — 분기 실적은 계절성·일회성 비용 등 노이즈가
    커서 "일시적 적자 = 부실"로 단정하기 어렵다는 판단. 리포트에는 참고정보로만
    표시하고, 밸류트랩 여부는 공시·뉴스와 함께 사람이 직접 보고 판단하게 한다.
  - **하락률 점수(최대 30점)**: 35% 하락 지점을 정점으로 하는 산 모양 곡선
    (`_price_score`, `PRICE_SCORE_PEAK_PCT`/`PRICE_SCORE_SLOPE`로 조정 가능).
    너무 얕은 조정도 너무 깊은 폭락도 점진적으로 감점될 뿐 하드 컷오프로 통째로
    제외되지 않는다.
  - **기술적 신호 점수(최대 40점)**: RSI 과매도/이동평균 골든크로스(단기·중기)/
    MACD 골든크로스/볼린저밴드 하단이탈 — 서로 겹치는 지표라 AND로 묶으면 항상
    0건에 가까워서, **OR 방식(신호 종류 수 × 가중치)** 으로 여러 신호가 겹칠수록
    가산점을 준다.
  - 총점(가격+기술, 최대 70점)이 0보다 큰 종목만 대상으로 상위 `TOP_N`을 뽑는다
    — 유니버스·가격 조회만 정상이면 "오늘은 없습니다"가 구조적으로 안 나온다.

### 구성

- `lib/universe.py` — 코스피200(네이버증권 entryJongmok.naver) + 코스닥150
  (네이버증권에 전용 페이지가 없어 코스닥 시가총액 상위 150종목으로 근사,
  그래도 실패 시 `kosdaq150_constituents.json` 로컬 폴백) 구성종목 조회
- `lib/market_data.py`의 `get_price_and_52w_range()` — 현재가 + 52주 고저 조회
- `lib/technical_indicators.py` — Yahoo Finance 가격 시계열로 이동평균/골든·
  데드크로스/RSI/MACD/거래량/볼린저밴드 계산, 각 신호에 강세(bullish)/약세
  (bearish)/중립(neutral) 태그를 붙임 (`bullish_signals`로 점수 계산에 사용)
- `lib/dart_client.py` — DART Open API로 종목코드→고유번호 매핑(로컬 캐시
  `dart_corp_codes.json`, git 미추적), 최근 공시, 직전 분기 실적(참고용) 조회
- `stock_screener.py` — 위를 엮어 전종목 점수 계산 → 랭킹 → 상위 TOP_N만 실적/
  공시/뉴스로 정보 보강 → 마크다운 리포트 생성 → 텔레그램 전송

topic_recommender.py의 "최근 선정 종목 제외(쿨다운)" 로직은 쓰지 않는다 — 어제도
상위권이었던 종목이라도 공시·뉴스·지표는 매일 바뀌므로 매번 다시 랭킹한다.

### 필요한 것

- `.env`(또는 PythonAnywhere 환경변수)에 `DART_API_KEY` 추가 — https://opendart.fss.or.kr 에서 무료 발급.
  **GitHub Actions로 검증할 때도 저장소 Secrets에 `DART_API_KEY`를 등록해야 한다** —
  2026-07-28 첫 실전 실행 때 이 값이 비어 있어서(시크릿 미등록) 확인됨.

### 검증 이력 (2026-07-28 GitHub Actions 실전 실행)

- **코스피200**: entryJongmok.naver(type=KPI200) 정상 동작 확인. 다만 실제 table
  class가 `type_5`가 아니라 `type_1`이고 종목명 링크에 `a.tltle` 클래스가 없어서
  "code=" 포함 링크로 직접 추출하도록 `lib/universe.py` 수정 완료.
- **코스닥150**: entryJongmok.naver에 `type=KOSDAQ150`, `type=KDQ150` 둘 다 시도했지만
  실제 데이터가 아니라 네이버의 범용 에러 페이지("일시적 오류로 페이지 접속이
  불가합니다")만 돌아옴 — 코스닥150 전용 조회 페이지 자체가 없는 것으로 판단.
  대신 코스닥 시가총액 상위 150종목(`sise_market_sum.naver?sosok=1`, 3페이지)으로
  근사하도록 수정 완료 (실제 종목명·코드 확인됨: 알테오젠, 에코프로비엠, 에코프로,
  레인보우로보틱스 등).
- **DART_API_KEY**: 이 실행에서 시크릿이 비어 있었음. 유니버스가 0건이라 실적
  조회 단계까지 도달하지 못해 실제 DART API 동작 여부는 아직 확인 안 됨 — 시크릿
  등록 후 재검증 필요.

### PythonAnywhere 배포 (기존 `invest/` 파이프라인과 분리)

1. Files 탭에서 새 디렉토리 생성(예: `invest-screener/`)
2. Bash 콘솔에서 `git clone` (또는 기존 저장소를 이 디렉토리에 새로 clone)
3. `cp .env.example .env` 후 `GEMINI_API_KEY`는 필요 없고 `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, `DART_API_KEY` 채워 넣기 (텔레그램 토큰은 기존 파이프라인과
   같은 채널을 쓸지, 별도 채널을 쓸지 선택 — 같은 채널이면 기존 `.env` 값 그대로 복사)
4. `pip install -r requirements.txt`
5. Tasks 탭에서 새 예약 작업 추가: `cd ~/invest-screener && git pull && python3 stock_screener.py`
   (기존 `invest/` 디렉토리의 예약 작업과 완전히 분리되어 있어 서로 영향 없음)

## 종목분석기 — 적정주가 판단 (`stock_analyzer.py`) — 별도 파이프라인

topic_recommender.py, stock_screener.py와 완전히 독립된 세 번째 파이프라인이다.
스케줄 배치가 아니라 **종목 하나를 지정해 그때그때 실행하는 도구**다 — 종목명 또는
종목코드를 입력하면 객관적 재무 데이터로 적정주가를 산출해 현재가와 비교하고
저평가/고평가/적정 구간을 판정한다.

```bash
python3 stock_analyzer.py 삼성전자          # 콘솔 출력 + analysis_reports/에 저장
python3 stock_analyzer.py 005930 --telegram  # 텔레그램으로도 전송
```

### 밸류에이션 방법 (`lib/valuation.py`)

성장률 전망 같은 주관적 가정이 필요한 방법(DCF, 배당할인모형)은 쓰지 않는다.
공시된 재무 데이터만으로 계산 가능한 2가지만 각각 독립적으로 산출해 평균을 낸다.

1. **PER 밸류에이션** — EPS × 동일업종 PER (같은 업종에 시장이 실제로 매기는
   배수를 그대로 적용)
2. **그레이엄 공식(Graham Number)** — sqrt(22.5 × EPS × BPS) (벤저민 그레이엄의
   보수적 적정주가 상한선 공식, PER 15배 × PBR 1.5배 = 22.5 가정, 업종 비교 데이터
   없이도 계산 가능)

두 방법의 평균을 적정주가 추정치로 삼아 현재가 대비 괴리율(%)로 판정한다
(+20%↑ 저평가 / -20%↓ 고평가 / 그 사이 적정 구간, `lib/valuation.py`의
`GAP_UNDERVALUED_PCT`/`GAP_OVERVALUED_PCT`로 조정 가능). 필요한 데이터(EPS·BPS·
동일업종 PER)가 없으면 해당 방법은 건너뛰고, 둘 다 산출 불가면 "판단 불가"로
명시한다 — 근거 없이 숫자를 만들어내지 않는다(가이드 4-4 원칙).

기술적 지표(RSI·이동평균 등)·직전 분기 실적·최근 공시·뉴스는 기존 lib를 재사용해
참고정보로 함께 보여주지만 밸류에이션 판정에는 반영하지 않는다 — 가격 추세와
적정가격은 별개 질문이라는 stock_screener.py와 같은 설계 원칙이다.

### 구성

- `lib/stock_search.py` — 종목명/종목코드 → (정식 종목명, 종목코드) 해석
  (6자리 숫자면 코드로 바로 취급, 그 외는 네이버증권 검색 결과 첫 매칭 채택)
- `lib/fundamentals.py` — 네이버증권 "기업현황" 탭에서 PER·EPS·PBR·BPS·동일업종
  PER·배당수익률 조회
- `lib/valuation.py` — 위 데이터로 적정주가 산출 + 판정
- `lib/market_data.py`의 `get_price_and_52w_range()` — 현재가 + 52주 고저 (기존 재사용)
- `lib/technical_indicators.py`, `lib/dart_client.py`, `lib/stock_discovery.py` —
  참고정보(기술적 지표, 실적/공시, 뉴스) 조회 (기존 재사용)
- `stock_analyzer.py` — 위를 엮어 마크다운 리포트 생성 (`analysis_reports/`에 저장) +
  콘솔 출력, `--telegram` 옵션으로 텔레그램 전송

### 검증 필요 (2026-07-31 작성, 아직 실전 미검증)

`lib/fundamentals.py`, `lib/stock_search.py`는 개발 환경 네트워크 제한으로 실제
네이버증권 페이지 마크업을 직접 확인하지 못한 채 작성됐다. `lib/market_data.py`의
52주 고저 조회가 그랬던 것처럼 실제 마크업이 예상과 다를 가능성이 있다.
`.github/workflows/stock_analyzer.yml`(workflow_dispatch, 종목명 입력)로 실행해
로그를 확인하고, 라벨을 못 찾으면 해당 모듈의 `_num_after` 탐색 범위/로직을
실제 구조에 맞게 조정할 것.
