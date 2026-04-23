# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

CPBC(삼일대로 330) 기준 반경 1km 이내 주차장 데이터를 모두의주차 API에서 수집하여 Google Sheets에 이력을 누적하고, GitHub Pages HTML 대시보드를 자동 생성하는 모니터링 시스템.

## 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# 로컬 실행 (credentials.json 필요)
python main.py
```

로컬 실행 시 프로젝트 루트에 `credentials.json`(Google 서비스 계정 키)이 있어야 한다. GitHub Actions에서는 `GOOGLE_CREDENTIALS` 시크릿에서 자동 생성된다.

## 아키텍처

**단일 파일 구조** — 모든 로직이 `main.py` 한 파일에 있다.

```
main.py          핵심 로직 전체
docs/index.html  자동 생성 대시보드 (직접 편집 금지)
snapshot.json    직전 실행 데이터 스냅샷 (변경 감지용)
history.json     실행 타임스탬프 누적 이력 (최대 365개)
```

**실행 흐름:**
1. `fetch_api()` — Modu Cloud API 호출 (geohash 기반 범위 쿼리)
2. `parse()` — 반경 필터링·정렬, 주차장/할인권 분리
3. `compare()` — `snapshot.json` 과 비교하여 변경사항 추출
4. `write_sheets()` — Google Sheets 3개 시트에 이력 append
5. `analyze_tickets()` — 500m 이내 파트너 주차장 할인권 평균가 대비 평화빌딩 적정성 분석
6. `get_ai_insight()` — Gemini API로 가격 전략 제안 (선택적, `GEMINI_API_KEY` 필요)
7. `build_html()` — 대시보드 HTML 생성 (전체 인라인 CSS+JS 포함)

**변경 감지 메커니즘:**
- `snapshot.json`이 없으면(`load_snap()` → `None`) `compare()`는 빈 리스트 반환
- `snapshot.json`과 `history.json`은 워크플로우에서 git에 커밋되어 다음 실행 시 재사용됨
- 이 두 파일이 커밋되지 않으면 변경 감지가 영구적으로 작동하지 않음

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
| `SHEET_ID` | `1E0llbaO...` | Google Sheets 문서 ID |

## Google Sheets 구조

`setup_sheets()`가 최초 실행 시 시트를 자동 생성한다.

- **요금이력** — 매 실행마다 전체 주차장 요금 append
- **할인권이력** — 매 실행마다 전체 할인권 append
- **변경이력** — 변경사항 발생 시에만 append

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

## HTML 대시보드

`build_html()`이 반환하는 문자열 전체가 대시보드다. CSS와 JS가 모두 인라인으로 포함되어 있으며, Python f-string 이중 중괄호(`{{`, `}}`)로 리터럴 `{`, `}`를 이스케이프한다. 대시보드 UI를 수정할 때는 이 함수 내부의 HTML/CSS/JS를 직접 편집한다.

**`docs/index.html` 동기화 주의:** `main.py`의 `build_html()`을 수정해도 `docs/index.html`은 GitHub Actions가 다음 실행될 때까지 갱신되지 않는다. 변경사항을 즉시 페이지에 반영해야 한다면 `docs/index.html`도 직접 같이 패치해야 한다.

## 모두의주차 딥링크 URL

대시보드에서 주차장 클릭 시 사용하는 URL 포맷:

```
https://app.modu.kr/map?type=P&id={parkinglotSeq}#sheet=1&event=0
```

지도에서 해당 주차장이 선택된 상태로 상세 시트가 열린다. `parkinglotSeq`는 API 응답의 `parkinglotSeq` 필드값이며, `parse()`에서 `seq`로 저장된다.
