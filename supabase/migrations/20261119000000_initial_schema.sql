-- =====================================================================
-- 자취방 체크 — 초기 스키마
-- 영역: ① 공공데이터(배치 전용) ② 사용자 ③ 알림
-- 규칙: snake_case, 단위는 컬럼명에(_man_won, _m2), 날짜 _date / 시각 _at / 불리언 is_
--       금액은 국토부 원본 그대로 만원 단위 정수
-- =====================================================================

create extension if not exists postgis with schema extensions;
create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------------
-- 공통: updated_at 자동 갱신
-- ---------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------
-- ENUM
-- ---------------------------------------------------------------------
create type public.housing_type as enum ('detached_multi_unit', 'row_house', 'officetel');
  -- detached_multi_unit = 단독/다가구, row_house = 연립다세대
create type public.contract_kind as enum ('new', 'renewal');
create type public.area_bucket as enum ('under_20m2', 'from_20_to_33m2', 'from_33_to_50m2', 'over_50m2');
create type public.auth_provider as enum ('kakao', 'google');
create type public.judgment_mode as enum ('quick_check', 'renewal_check');
create type public.input_source as enum ('manual', 'capture');
create type public.verdict as enum ('fair', 'slightly_high', 'high');
create type public.judged_by as enum ('model', 'stats_fallback');
create type public.extraction_status as enum ('success', 'partial', 'failed');
create type public.subscription_kind as enum ('area_watch', 'renewal_watch');
create type public.notification_channel as enum ('email');
create type public.notification_status as enum ('sent', 'failed');

-- =====================================================================
-- ① 공공데이터 영역 — 배치(service role)만 쓰고, 웹은 읽기만
-- =====================================================================

create table public.regions (
  region_code   char(10) primary key,               -- 법정동코드
  sigungu_code  char(5)  not null,                  -- 국토부 API 조회 단위
  sido_name     text     not null,
  sigungu_name  text     not null,
  dong_name     text     not null,
  boundary      extensions.geometry(MultiPolygon, 4326),
  centroid      extensions.geometry(Point, 4326)
);
create index regions_sigungu_code_idx on public.regions (sigungu_code);
create index regions_boundary_gix     on public.regions using gist (boundary);

create table public.rent_transactions (
  id                    bigint generated always as identity primary key,
  source_hash           text not null unique,        -- 거래 ID가 없어 핵심 필드 해시로 중복 방지
  region_code           char(10) not null references public.regions (region_code),
  housing_type          public.housing_type not null,
  jibun                 text,                        -- 다가구는 일부만 제공
  area_m2               numeric(7, 2) not null check (area_m2 > 0),
  floor                 smallint,
  build_year            smallint check (build_year between 1900 and 2100),
  deposit_man_won       integer not null check (deposit_man_won >= 0),
  monthly_rent_man_won  integer not null check (monthly_rent_man_won >= 0),  -- 0 = 전세
  contract_date         date not null,
  contract_kind         public.contract_kind,
  prev_deposit_man_won  integer check (prev_deposit_man_won >= 0),
  prev_rent_man_won     integer check (prev_rent_man_won >= 0),
  ingested_at           timestamptz not null default now()
);
create index rent_transactions_lookup_idx
  on public.rent_transactions (region_code, housing_type, contract_date desc);

create table public.region_rent_stats (
  region_code                    char(10) not null references public.regions (region_code),
  housing_type                   public.housing_type not null,
  area_bucket                    public.area_bucket not null,
  period_month                   date not null check (extract(day from period_month) = 1),
  sample_count                   integer not null check (sample_count >= 0),
  median_converted_rent_man_won  integer not null,
  p25_converted_rent_man_won     integer not null,
  p75_converted_rent_man_won     integer not null,
  computed_at                    timestamptz not null default now(),
  primary key (region_code, housing_type, area_bucket, period_month),
  check (p25_converted_rent_man_won <= median_converted_rent_man_won
     and median_converted_rent_man_won <= p75_converted_rent_man_won)
);

create table public.model_versions (
  id                  smallint generated always as identity primary key,
  version_tag         text not null unique,            -- 예: rent-lgbm-2026-11-19
  train_period_start  date not null,
  train_period_end    date not null,
  val_mape            numeric(5, 2),
  test_mape           numeric(5, 2),
  artifact_uri        text not null,
  is_active           boolean not null default false,
  trained_at          timestamptz not null default now(),
  check (train_period_start <= train_period_end)
);
-- 활성 모델은 동시에 1개만
create unique index model_versions_single_active_idx
  on public.model_versions (is_active) where is_active;

-- =====================================================================
-- ② 사용자 영역
-- =====================================================================

create table public.profiles (
  user_id                  uuid primary key references auth.users (id) on delete cascade,
  display_name             text,
  auth_provider            public.auth_provider not null,
  is_email_alert_enabled   boolean not null default false,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);
create trigger profiles_set_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

create table public.capture_extractions (
  id                uuid primary key default gen_random_uuid(),
  image_sha256      text not null unique,             -- 같은 이미지는 LLM 재호출 없이 캐시 사용
  extracted_fields  jsonb not null default '{}'::jsonb,
  status            public.extraction_status not null,
  llm_model         text not null,
  created_at        timestamptz not null default now()
  -- 원본 이미지는 저장하지 않는다 (중개사 연락처 등 개인정보 가능성)
);

create table public.judgments (
  id                         uuid primary key default gen_random_uuid(),  -- 공유 링크 값
  user_id                    uuid references public.profiles (user_id) on delete set null,
  anonymous_id               uuid,
  mode                       public.judgment_mode not null,
  input_source               public.input_source not null,
  region_code                char(10) not null references public.regions (region_code),
  housing_type               public.housing_type not null,
  area_m2                    numeric(7, 2) not null check (area_m2 > 0),
  floor                      smallint,
  build_year                 smallint check (build_year between 1900 and 2100),
  deposit_man_won            integer not null check (deposit_man_won >= 0),
  monthly_rent_man_won       integer not null check (monthly_rent_man_won >= 0),
  maintenance_fee_man_won    integer check (maintenance_fee_man_won >= 0),
  predicted_rent_man_won     integer not null,
  predicted_lower_man_won    integer not null,
  predicted_upper_man_won    integer not null,
  verdict                    public.verdict not null,
  judged_by                  public.judged_by not null,
  model_version_id           smallint references public.model_versions (id),
  capture_extraction_id      uuid references public.capture_extractions (id) on delete set null,
  created_at                 timestamptz not null default now(),
  check (user_id is not null or anonymous_id is not null),
  check (predicted_lower_man_won <= predicted_rent_man_won
     and predicted_rent_man_won <= predicted_upper_man_won),
  -- 모델 판정이면 모델 버전 필수, 통계 대체 판정이면 없음
  check ((judged_by = 'model' and model_version_id is not null)
      or (judged_by = 'stats_fallback' and model_version_id is null)),
  -- 캡처 판정이면 추출 결과 연결
  check (input_source = 'manual' or capture_extraction_id is not null)
);
create index judgments_user_created_idx      on public.judgments (user_id, created_at desc) where user_id is not null;
create index judgments_anonymous_id_idx      on public.judgments (anonymous_id) where anonymous_id is not null;

create table public.usage_counters (
  subject_key    text not null,     -- ip:<sha256> | anon:<uuid> | user:<uuid>  (IP 원문 저장 금지)
  usage_date     date not null,
  capture_count  integer not null default 0 check (capture_count >= 0),
  primary key (subject_key, usage_date)
);

-- =====================================================================
-- ③ 알림 영역
-- =====================================================================

create table public.subscriptions (
  id                        uuid primary key default gen_random_uuid(),
  user_id                   uuid not null references public.profiles (user_id) on delete cascade,
  kind                      public.subscription_kind not null,
  region_code               char(10) not null references public.regions (region_code),
  housing_type              public.housing_type not null,
  area_bucket               public.area_bucket not null,
  current_deposit_man_won   integer check (current_deposit_man_won >= 0),
  current_rent_man_won      integer check (current_rent_man_won >= 0),
  renewal_date              date,
  change_threshold_pct      numeric(4, 1) not null default 5.0 check (change_threshold_pct > 0),
  last_notified_stat_month  date,
  is_active                 boolean not null default true,
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now(),
  -- 종류별로 필요한 컬럼만 채워지게
  check (
    (kind = 'renewal_watch'
       and current_deposit_man_won is not null
       and current_rent_man_won is not null
       and renewal_date is not null)
    or
    (kind = 'area_watch'
       and current_deposit_man_won is null
       and current_rent_man_won is null
       and renewal_date is null)
  )
);
create index subscriptions_user_idx   on public.subscriptions (user_id);
create index subscriptions_active_idx on public.subscriptions (region_code, housing_type, area_bucket) where is_active;
create trigger subscriptions_set_updated_at
  before update on public.subscriptions
  for each row execute function public.set_updated_at();

create table public.notification_logs (
  id               bigint generated always as identity primary key,
  subscription_id  uuid not null references public.subscriptions (id) on delete cascade,
  channel          public.notification_channel not null,
  dedupe_key       text not null unique,          -- 예: <subscription_id>:2026-12
  status           public.notification_status not null,
  error_message    text,
  sent_at          timestamptz not null default now(),
  check (status = 'sent' or error_message is not null)
);
create index notification_logs_subscription_idx on public.notification_logs (subscription_id, sent_at desc);

-- =====================================================================
-- 함수 (각 함수는 한 가지 일만)
-- =====================================================================

-- 가입 시 프로필 자동 생성
create or replace function public.create_profile_for_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (user_id, display_name, auth_provider)
  values (
    new.id,
    coalesce(new.raw_user_meta_data ->> 'name', new.raw_user_meta_data ->> 'full_name'),
    (new.raw_app_meta_data ->> 'provider')::public.auth_provider
  );
  return new;
end;
$$;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.create_profile_for_new_user();

-- 공유 링크 조회: id를 아는 사람만 1건 조회, 식별 정보(user_id, anonymous_id)는 제외
create or replace function public.get_shared_judgment(p_judgment_id uuid)
returns table (
  id uuid,
  mode public.judgment_mode,
  region_code char(10),
  housing_type public.housing_type,
  area_m2 numeric,
  floor smallint,
  deposit_man_won integer,
  monthly_rent_man_won integer,
  maintenance_fee_man_won integer,
  predicted_rent_man_won integer,
  predicted_lower_man_won integer,
  predicted_upper_man_won integer,
  verdict public.verdict,
  judged_by public.judged_by,
  created_at timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
  select j.id, j.mode, j.region_code, j.housing_type, j.area_m2, j.floor,
         j.deposit_man_won, j.monthly_rent_man_won, j.maintenance_fee_man_won,
         j.predicted_rent_man_won, j.predicted_lower_man_won, j.predicted_upper_man_won,
         j.verdict, j.judged_by, j.created_at
  from public.judgments j
  where j.id = p_judgment_id;
$$;

-- 로그인 후 비로그인 시절 판정을 내 계정으로 연결
create or replace function public.claim_anonymous_judgments(p_anonymous_id uuid)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_claimed_count integer;
begin
  if auth.uid() is null then
    raise exception 'login required';
  end if;

  update public.judgments
     set user_id = auth.uid()
   where anonymous_id = p_anonymous_id
     and user_id is null;

  get diagnostics v_claimed_count = row_count;
  return v_claimed_count;
end;
$$;

-- 캡처 사용량을 원자적으로 1 올리고, 한도 안이면 true
create or replace function public.try_increment_capture_usage(p_subject_key text, p_daily_limit integer)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_new_count integer;
begin
  insert into public.usage_counters as uc (subject_key, usage_date, capture_count)
  values (p_subject_key, current_date, 1)
  on conflict (subject_key, usage_date)
  do update set capture_count = uc.capture_count + 1
     where uc.capture_count < p_daily_limit
  returning uc.capture_count into v_new_count;

  return v_new_count is not null and v_new_count <= p_daily_limit;
end;
$$;

-- =====================================================================
-- 권한 (RLS) — 정책이 없는 테이블은 service role(배치·서버)만 접근
-- =====================================================================

alter table public.regions              enable row level security;
alter table public.rent_transactions    enable row level security;
alter table public.region_rent_stats    enable row level security;
alter table public.model_versions       enable row level security;
alter table public.profiles             enable row level security;
alter table public.capture_extractions  enable row level security;
alter table public.judgments            enable row level security;
alter table public.usage_counters       enable row level security;
alter table public.subscriptions        enable row level security;
alter table public.notification_logs    enable row level security;

-- 공공 집계는 누구나 읽기 (동네 페이지)
create policy regions_read_all            on public.regions            for select to anon, authenticated using (true);
create policy region_rent_stats_read_all  on public.region_rent_stats  for select to anon, authenticated using (true);

-- 프로필: 본인만
create policy profiles_read_own   on public.profiles for select to authenticated using (user_id = (select auth.uid()));
create policy profiles_update_own on public.profiles for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

-- 판정: 목록은 본인 것만. 공유 조회는 get_shared_judgment() 로만. 쓰기는 서버만.
create policy judgments_read_own on public.judgments for select to authenticated using (user_id = (select auth.uid()));

-- 구독: 본인 것만 CRUD
create policy subscriptions_read_own   on public.subscriptions for select to authenticated using (user_id = (select auth.uid()));
create policy subscriptions_insert_own on public.subscriptions for insert to authenticated with check (user_id = (select auth.uid()));
create policy subscriptions_update_own on public.subscriptions for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy subscriptions_delete_own on public.subscriptions for delete to authenticated using (user_id = (select auth.uid()));

-- 발송 기록: 내 구독 것만 읽기
create policy notification_logs_read_own on public.notification_logs for select to authenticated
  using (exists (
    select 1 from public.subscriptions s
    where s.id = notification_logs.subscription_id
      and s.user_id = (select auth.uid())
  ));

-- 함수 실행 권한
revoke execute on function public.get_shared_judgment(uuid)                from public;
revoke execute on function public.claim_anonymous_judgments(uuid)          from public;
revoke execute on function public.try_increment_capture_usage(text, integer) from public;
revoke execute on function public.create_profile_for_new_user()            from public;
grant  execute on function public.get_shared_judgment(uuid)       to anon, authenticated;
grant  execute on function public.claim_anonymous_judgments(uuid) to authenticated;
grant  execute on function public.try_increment_capture_usage(text, integer) to service_role;
