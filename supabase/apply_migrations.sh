#!/usr/bin/env bash
# supabase/migrations/*.sql 을 이름순으로, 아직 적용 안 된 것만 하나씩(각각 한 트랜잭션) 적용한다.
# 사용: DATABASE_URL=... bash supabase/apply_migrations.sh
set -euo pipefail
: "${DATABASE_URL:?DATABASE_URL 이 필요합니다}"
cd "$(dirname "$0")/migrations"

PSQL=(psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -X)
"${PSQL[@]}" -c "create table if not exists public.schema_migrations (
  version text primary key,
  applied_at timestamptz not null default now()
);
alter table public.schema_migrations enable row level security;"

for file in *.sql; do
  version="${file%%_*}"
  already=$("${PSQL[@]}" -tA -c "select 1 from public.schema_migrations where version = '$version'")
  if [ "$already" = "1" ]; then
    echo "건너뜀: $file"
    continue
  fi
  echo "적용: $file"
  "${PSQL[@]}" -1 \
    -c "set search_path = public, extensions" \
    -f "$file" \
    -c "insert into public.schema_migrations (version) values ('$version')"
done
echo "마이그레이션 완료"
