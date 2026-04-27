# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

CPBC(삼일대로 330, 평화빌딩) 기준 반경 1km 이내 주차장 데이터를 모두의주차 API에서 수집하여 Google Sheets에 이력을 누적하고, GitHub Pages HTML 대시보드를 자동 생성하는 모니터링 시스템.

**운영 주체:** 평화빌딩 주차장 관리자 (CPBC 입주 언론사)

## 실행 방법

```bash
pip install -r requirements.txt
python main.py   # 로컬 실행 (프로젝트 루트에 credentials.json 필요)
```

GitHub Actions에서는 `GOOGLE_CREDENTIALS` 시크릿으로 credentials.json을 자동 생성한다.

## 아키텍처

**단일 파일 구조** — 모든 로직이 `main.py` 한 파일에 있다.

```
main.py          핵심 로직 전체
docs/index.html  자동 생성 대시보드 (직접 편집 금지)
snapshot.json    직전 실행 데이터 스냅샷 (변경 감지용, git 추적)
history.json     실행 타임스탬프 누적 이력 최대 365개 (git 추적)
```

**실행 흐름:**
1. `fetch_api()` — Modu Cloud API 호출 (geohash 기반 범위 쿼리)
2. `parse()` — 반경 필터링·정렬, 주차장/할인권 분리. **무료(p60==0) 주차장은 제외**
3. `compare()` — `snapshot.json`과 비교하여 변경사항 추출
4. `write_sheets()` — Google Sheets 3개 시트에 이력 append
5. `analyze_tickets()` — **300m** 이내 파트너 주차장 할인권 평균가 대비 평화빌딩 적정성 분석
6. `get_ai_insight()` — Gemini API로 가격 전략 제안 (503 오류 시 5s·10s 대기 후 2회 재시도)
7. `build_html()` — 대시보드 HTML 생성 (전체 인라인 CSS+JS 포함)

**변경 감지 메커니즘:**
- `snapshot.json`이 없으면 `compare()`는 빈 리스트 반환
- `snapshot.json`과 `history.json`은 워크플로우에서 반드시 git 커밋되어야 다음 실행 시 재사용됨
- run.yml의 `git add` 대상에 두 파일이 포함되어 있어야 변경 감지가 작동함

## GitHub Actions 스케줄

`.github/workflows/run.yml` — 하루 5회 자동 실행 (KST 기준):

| KST | UTC cron |
|-----|----------|
| 00:00 | `0 15 * * *` |
| 09:00 | `0 0 * * *` |
| 10:00 | `0 1 * * *` |
| 11:00 | `0 2 * * *` |
| 12:00 | `0 3 * * *` |

## 주요 상수 (main.py 상단)

| 상수 | 값 | 설명 |
|------|----|------|
| `CPBC_LAT/LNG` | 37.5643171 / 126.9881729 | 기준 좌표 (삼일대로 330) |
| `RADIUS` | 1000 | 수집 반경 (미터) |
| `MY_LOT_DEFAULT` | `"평화빌딩"` | 우리 주차장 식별 키워드 (대시보드 강조·AI 분석 기준) |
| `SHEET_ID` | `1E0llbaO...` | Google Sheets 문서 ID |

## 평화빌딩 주차장 운영 특성 (AI 분석 맥락)

AI 프롬프트(`get_ai_insight()`)에 아래 맥락이 반영되어 있다. 수정 시 유지할 것:

- 낮 시간대 고정 주차 수요가 높음 (방송 출연진, 취재 차량, 직원 등)
- 고정 점유 구조이므로 외부 이용객 대상 **빠른 회전율**이 수익에 직결됨
- **당일권이 주변 대비 저렴하거나 평균 수준이면** 종일 고정 점유 이용자가 몰려 회전율이 오히려 떨어지는 역효과 발생
- 따라서 당일권은 주변 평균보다 **다소 높게** 유지하는 것이 유리
- AI 분석 비교 반경: **300m** 이내 파트너 주차장 기준

## Google Sheets 구조

`setup_sheets()`가 최초 실행 시 시트를 자동 생성한다.

- **요금이력** — 매 실행마다 전체 주차장 요금 append
- **할인권이력** — 매 실행마다 전체 할인권 append
- **변경이력** — 변경사항 발생 시에만 append

## 대시보드 주요 기능

`build_html()`이 반환하는 문자열 전체가 대시보드. CSS·JS 모두 인라인. Python f-string 이중 중괄호(`{{`, `}}`)로 `{`, `}`를 이스케이프한다.

| 기능 | 설명 |
|------|------|
| 거리 슬라이더 | 기본값 300m, 최대 1000m까지 조정 가능 |
| 기계식 제외 버튼 | 요금·할인권 탭 각각 독립 토글, 다른 필터와 복수 적용 가능 |
| 평화빌딩 행 강조 | 노란색 왼쪽 테두리·배경·글자색으로 즉시 식별 |
| 변경사항 감지창 | 업체별 accordion으로 묶기, 높이 150px (스크롤) |

**`docs/index.html` 동기화 주의:** `build_html()`을 수정해도 `docs/index.html`은 Actions 다음 실행 시까지 갱신되지 않는다. 즉시 반영이 필요하면 두 파일을 함께 수정한다.

## 새 계정/저장소 초기 세팅

코드 자체는 자동이지만, 아래 3가지는 최초 1회 수동 설정이 필요하다.

### 1. GitHub Secrets 등록
저장소 **Settings → Secrets and variables → Actions** 에서 등록:

| Secret | 용도 |
|--------|------|
| `GOOGLE_CREDENTIALS` | Google 서비스 계정 JSON 전체 내용 (Sheets + Drive 권한 필요) |
| `GEMINI_API_KEY` | Gemini API 키 (없으면 AI 분석만 스킵, 나머지 정상 동작) |

### 2. GitHub Pages 활성화
저장소 **Settings → Pages** 에서:
- Source: `Deploy from a branch`
- Branch: `main` / `docs` 폴더

### 3. Google Sheets 공유 설정
`SHEET_ID`에 해당하는 Google Sheets 문서에 서비스 계정 이메일을 **편집자**로 공유 추가.
