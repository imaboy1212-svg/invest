# WP투자정보본부 주제 후보 자동화 파이프라인

wp-invest-article-builder 스킬(종목리포트/마켓칼럼/IPO)의 **주제 추천 단계(Step 1)** 만 자동화하는 파이프라인이다.
매일 정해진 시각에 시장 데이터와 뉴스를 수집해 팀별 주제 후보 3건(종목리포트/마켓칼럼/IPO 각 1건)과 상세 브리핑을 생성하고,
GitHub에 커밋 + 텔레그램(@investwellth)으로 전송한다.

**이 파이프라인이 하지 않는 것**: 기사 본문 작성, 워드프레스 발행. 그 단계는 사람이 Claude.ai에서 wp-invest-article-builder 스킬로 직접 진행한다.

## 실행 흐름

1. 날짜/요일 검증 (평일 장마감 후 / 주말)
2. Yahoo Finance + pykrx + 네이버증권으로 지수·종목 조회 및 교차검증
3. 증권 뉴스 크롤링 (한국경제 → 매일경제 → 서울경제 → 파이낸셜뉴스)
4. Gemini API로 팀별 주제 후보 3건 + 상세 브리핑 생성
5. `completed_topics.json` 기준으로 완료 주제 제외
6. 상세 브리핑 마크다운을 `briefings/`에 생성 및 커밋
7. 텔레그램으로 요약 메시지 + 브리핑 파일 전송

## 스케줄 (GitHub Actions)

- 평일: UTC 06:40 (KST 15:40, 장마감 후)
- 토·일: UTC 11:00 (KST 20:00, 금요일 데이터 기준 + 주말 뉴스 반영)

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
