# 자취방 체크 — 프로젝트 규칙

보증금·월세·면적(또는 매물 캡처)을 넣으면 국토부 실거래 데이터로 학습한 모델이
"이 방이 동네 시세 대비 비싼지"를 영수증 한 장으로 판정해 주는 서비스.
성남시에서 시작해 수도권 대학가로 확장한다.

이 문서는 사람과 AI 에이전트 모두를 위한 규칙이다. 코드를 쓰기 전에 끝까지 읽는다.
규칙과 다른 선택이 필요하면 코드보다 이 문서를 먼저 고친다.

---

## 1. 목표 (모든 판단의 기준)

1. **트래픽이 몰려도 안정적으로 동작한다.** 사용자 요청은 캐시·사전 집계로 처리하고, 공공 API 호출과 재학습은 배치에서만 한다.
2. **유지보수가 쉽다.** 파일·함수는 책임 하나. 이름만 보고 역할을 알 수 있어야 한다.
3. **하나가 죽어도 나머지는 산다.** 추론 서버·LLM이 실패해도 통계 기반 대체 판정이 동작한다.

---

## 2. 저장소 구조

```
jachwi-check/
├── CLAUDE.md
├── apps/
│   ├── web/              # Next.js (Vercel) — 화면, 동네 페이지, API 라우트
│   └── inference/        # FastAPI (Cloud Run) — 월세 예측, 캡처 추출
├── packages/
│   └── jachwi_core/      # 공통 Python 패키지 — 계산 규칙의 단일 출처
├── pipelines/            # Python 배치 (GitHub Actions) — 수집·집계·학습·알림
├── ml/                   # 실험 공간 (노트북, 실험 기록)
├── supabase/
│   ├── migrations/       # 스키마의 유일한 출처
│   └── tests/
└── .github/workflows/
```

- 폴더 = 배포 단위. 한 앱의 변경이 다른 앱 배포를 요구하면 설계를 다시 본다.
- **운영 코드는 `ml/`을 import하지 않는다.** 실험에서 확정된 로직은 `packages/jachwi_core` 또는 `pipelines/`로 옮긴다.

---

## 3. apps/web 구조와 계층

```
apps/web/
├── app/                      # 라우팅과 화면 조립만
│   ├── page.tsx              # 홈: 모드 선택
│   ├── check/                # A. 매물 거르기
│   ├── renewal/              # D. 갱신 점검
│   ├── r/[judgmentId]/       # 공유 영수증
│   ├── rent/[regionCode]/    # 동네 월세 명세서 (정적 생성, SEO)
│   ├── me/                   # 판정 기록, 구독 관리 (로그인)
│   ├── api/
│   ├── sitemap.ts, robots.ts
│   └── llms.txt/route.ts
├── features/
│   ├── judgment/             # 판정
│   ├── capture/              # 캡처 업로드 → 추출 → 사용량
│   ├── region-stats/         # 동네 통계
│   ├── subscription/         # 구독
│   └── auth/                 # 로그인, 비로그인 판정 계정 연결
├── shared/
│   ├── ui/
│   ├── lib/
│   │   ├── supabase/         # server.ts / browser.ts 분리
│   │   ├── inference-client.ts
│   │   └── money.ts
│   └── config/env.ts
└── tests/
```

각 feature 폴더 구성:

```
features/judgment/
├── components/           # ReceiptCard.tsx, VerdictStamp.tsx, RangeBar.tsx
├── judgment.service.ts   # 흐름 조율
├── judgment.repository.ts# 테이블 하나 읽기/쓰기
├── judgment.schema.ts    # zod 입력 검증
├── judgment.types.ts
└── index.ts              # 공개 API — 다른 feature는 이것만 import
```

### 계층 규칙

| 계층 | 한다 | 하지 않는다 |
|---|---|---|
| `app/` 페이지·API 라우트 | 요청 받기, schema 검증, service 호출, 화면 조립 | DB 접근, 비즈니스 로직 |
| `*.service.ts` | 흐름 조율 (모델 실패 시 통계 대체 등) | SQL, UI 코드 |
| `*.repository.ts` | 테이블 하나의 읽기·쓰기 | 판단 로직 |
| `components/` | 표시 (데이터는 props로) | 데이터 fetch |

- 의존 방향: `app → features → shared`. 역방향 import 금지.
- feature끼리는 상대 feature의 `index.ts`(공개 API)만 import한다. 내부 파일 직접 import 금지.
- Supabase service role 키는 서버 코드(`shared/lib/supabase/server.ts`)에서만 쓴다. 브라우저 번들에 들어가면 안 된다.

---

## 4. Python 구조 (inference / pipelines / packages)

```
packages/jachwi_core/
├── money.py          # 환산 월세 (전월세전환율)
├── area_bucket.py    # 면적 → 면적구간 (DB enum area_bucket과 동일)
├── features.py       # 모델 입력 피처 — 학습·추론 공용
└── schemas.py        # pydantic 입출력 구조

apps/inference/
├── main.py
├── routers/          # predict_router.py, extract_router.py
├── services/         # rent_predictor.py, listing_extractor.py
├── config.py
└── tests/

pipelines/
├── collect_rent_transactions.py
├── aggregate_region_stats.py
├── train_rent_model.py
├── send_subscription_alerts.py
├── clients/          # molit_client.py, db_client.py
└── tests/
```

- **피처 계산은 `jachwi_core.features` 한 곳에서만.** 학습과 추론이 다른 코드로 피처를 만들면 train/serve skew가 생긴다.
- **배치 파일 하나 = 단계 하나.** Actions에서 `수집 → 집계 → 학습 → 알림` 순서로 잇는다.
- **모든 배치는 멱등이다.** 여러 번 돌려도 결과가 같아야 한다 (`source_hash`, `dedupe_key`, upsert).
- **모델 승격:** 새 모델의 test set MAPE가 활성 모델보다 좋을 때만 `is_active`를 바꾼다.

---

## 5. 이름 규칙

### 공통
- 이름은 **무엇을 하는지/무엇인지**를 말한다. `data`, `info`, `temp`, `handle`, `process`, `util` 단독 사용 금지.
- 약어 금지 (예외: `id`, `url`, `api`, `db`, `llm`, `sha256`). `cnt` → `count`, `amt` → `amount`.
- **단위가 있는 값은 이름에 단위를 붙인다:** `depositManWon` / `deposit_man_won`, `areaM2` / `area_m2`, `timeoutMs`.
- 불리언은 `is` / `has` / `can` / `should`로 시작: `isActive`, `hasCapture`.
- 함수는 동사로 시작: `fetchRegionStats`, `parseListingText`, `calculateConvertedRent`.
  - `get` = 이미 있는 값 반환(가벼움), `fetch` = 네트워크·DB 조회, `calculate` = 순수 계산,
    `create` / `update` / `delete` = 쓰기, `parse` = 문자열 → 구조, `format` = 구조 → 표시 문자열,
    `validate` = 검사 후 에러, `is…` = 검사 후 불리언.
- 금액 표시 문자열은 `format…`만 만든다. 계산 함수는 숫자만 다룬다.

### TypeScript (apps/web)
| 대상 | 규칙 | 예 |
|---|---|---|
| 변수·함수 | camelCase | `predictedRentManWon`, `fetchJudgment()` |
| 타입·인터페이스·컴포넌트 | PascalCase | `Judgment`, `ReceiptCard` |
| 상수 | UPPER_SNAKE | `CAPTURE_DAILY_LIMIT` |
| 컴포넌트 파일 | PascalCase.tsx | `ReceiptCard.tsx` |
| 그 외 파일 | kebab-case + 역할 접미사 | `judgment.service.ts`, `inference-client.ts` |
| 라우트 폴더 | kebab-case | `app/rent/[regionCode]/` |

### Python
| 대상 | 규칙 | 예 |
|---|---|---|
| 변수·함수·모듈 | snake_case | `fetch_rent_transactions()` |
| 클래스 | PascalCase | `RentPredictor` |
| 상수 | UPPER_SNAKE | `MOLIT_PAGE_SIZE` |
| 배치 파일 | 동사_대상.py | `collect_rent_transactions.py` |

### DB (Postgres)
- 테이블: 복수형 snake_case (`judgments`). 컬럼: snake_case.
- 날짜 `_date`, 시각 `_at`, 불리언 `is_`, 외래키 `<참조테이블 단수>_id` (`model_version_id`).
- 금액은 **만원 단위 정수** (`_man_won`). 국토부 원본 단위 그대로. 표시할 때만 변환한다.
- enum 값은 영어 snake_case (`slightly_high`). 한국어 라벨은 web의 표시 계층에서만.
- 스키마 변경은 **반드시 새 마이그레이션 파일로.** 이미 적용된 마이그레이션은 수정하지 않는다.

### DB ↔ 코드 경계
- DB의 snake_case는 repository에서 camelCase로 바꿔 넘긴다. service와 component는 snake_case를 보지 않는다.

---

## 6. 함수와 파일 크기

- 함수 하나는 일 하나. "그리고"가 들어가는 설명이 나오면 쪼갠다.
  - 나쁨: `collectAndSaveAndAggregate()` → 좋음: `fetchMolitPage()`, `upsertRentTransactions()`, `aggregateRegionStats()`
- 함수 40줄, 파일 300줄을 넘기면 분리를 검토한다 (하드 리밋 아님, 신호).
- 매직 넘버 금지. 한도·임계값은 이름 있는 상수 또는 env로.

---

## 7. 안정성 규칙

- 외부 호출(추론 서버, LLM, 국토부 API)은 **모두 타임아웃 + 재시도 정책 + 실패 시 동작**을 명시한다.
- 판정 흐름: 모델 호출 → 실패 시 `region_rent_stats` 기반 대체 판정(`judged_by = 'stats_fallback'`). 사용자는 판정을 항상 받는다.
- LLM 캡처 추출은 `try_increment_capture_usage()`로 한도 확인 후 호출, 같은 이미지(`image_sha256`)는 캐시 사용, 실패하면 직접 입력 화면으로.
- 동네 페이지는 정적 생성 + 재검증(ISR). 사용자 요청이 공공 API를 직접 부르는 경로는 없다.
- 원본 캡처 이미지, IP 원문은 저장하지 않는다.

---

## 8. 데이터와 법적 원칙

- 데이터는 공식 오픈 API·공공 파일만. 민간 부동산 사이트 크롤링 금지 (실거래가 공개시스템 사이트도 스크래핑 금지 → API 사용).
- 모든 페이지 하단에 데이터 출처 표시.
- 매물 게시·중개 연결·수수료 모델 금지 (공인중개사법).
- 판정은 "참고용"임을 영수증에 표기. 표본이 적으면(`sample_count` 기준) 범위를 넓히고 명시.

---

## 9. 디자인 원칙 (영수증 컨셉)

- 판정 결과 = 영수증 한 장: 항목(보증금·월세·관리비·환산 월세) + 적정가 범위 막대 + 판정 도장.
- 판정 색은 `fair` / `slightly_high` / `high` 3단계만.
- 감열지 질감·모노스페이스 숫자·점선·지그재그 아랫단은 **판정 카드에만**. 나머지 UI는 담백하게.
- 공유 OG 이미지도 영수증 모양. 동네 페이지는 "월세 명세서" 문법.
- 레퍼런스(Apartment List, Zillow, Zumper, 집품, 호갱노노, 토스)는 기능 구조만 참고하고 시각 표현은 따라 하지 않는다.

---

## 10. 작업 방식 (에이전트 포함)

- 작업 전 이 문서와 관련 feature의 `index.ts`를 먼저 읽는다.
- 한 PR = 한 목적. 리팩터링과 기능 추가를 섞지 않는다.
- 새 기능은 테스트와 함께: service는 단위 테스트, repository는 로컬 Supabase 통합 테스트, 배치는 멱등성 테스트(두 번 실행해 결과 동일).
- 스키마 변경 시 `supabase/tests/`의 RLS 테스트를 갱신하고 통과시킨다.
- 커밋 메시지: `<영역>: <무엇을>` — 예 `web/judgment: 통계 대체 판정 추가`, `pipelines: 국토부 수집 재시도`.
