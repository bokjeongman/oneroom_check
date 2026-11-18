# 자취방 체크 (oneroom_check)

보증금·월세·면적이나 매물 캡처를 넣으면, 국토부 실거래 데이터로 학습한 모델이
"이 방이 동네 시세 대비 비싼지"를 영수증 한 장으로 판정해 주는 서비스.

- 지역: 성남시 → 수도권 대학가
- 스택: Next.js(Vercel) · Supabase(Postgres/PostGIS/Auth) · FastAPI(Cloud Run) · Python 배치(GitHub Actions)
- 규칙과 구조: [CLAUDE.md](./CLAUDE.md)

## 데이터 출처
국토교통부 단독/다가구·연립다세대·오피스텔 전월세 실거래가 자료 (공공데이터포털)

## 로컬 DB 검증
```bash
psql -d <testdb> -f supabase/tests/00_supabase_stub.sql   # Supabase 환경 흉내
for f in supabase/migrations/*.sql; do psql -d <testdb> -f "$f"; done
psql -d <testdb> -f supabase/tests/01_behavior_tests.sql
psql -d <testdb> -f supabase/tests/02_rent_transactions_tests.sql
```
