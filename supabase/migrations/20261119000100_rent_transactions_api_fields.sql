-- =====================================================================
-- 002: 국토부 전월세 API 실제 응답에 맞춰 rent_transactions 보강
--
-- 확인된 사실 (2026-11-19, 성남 수정구 2026-10 응답):
--  - 응답에 10자리 법정동코드가 없다 → sggCd(5) + umdNm(동 이름)로 regions 매핑
--  - 단독/다가구는 전용면적이 없고 건물 연면적(totalFloorAr)만 온다
--  - 연립다세대 floor = -1 은 반지하
--  - 계약기간("26.11~27.10"), 갱신요구권 사용 여부(useRRRight) 제공
-- =====================================================================

create type public.house_subtype as enum ('detached', 'multi_unit', 'row_house', 'multiplex');
  -- detached = 단독, multi_unit = 다가구, row_house = 연립, multiplex = 다세대 (오피스텔은 null)
create type public.area_kind as enum ('exclusive', 'total_floor');
  -- exclusive = 전용면적, total_floor = 건물 연면적 (단독/다가구)

alter table public.rent_transactions
  add column sgg_code              char(5),
  add column umd_name              text,
  add column house_subtype         public.house_subtype,
  add column area_kind             public.area_kind,
  add column building_name         text,
  add column contract_start_month  date,
  add column contract_end_month    date,
  add column is_renewal_right_used boolean;

-- 신규 테이블이라 기존 행이 없으므로 바로 not null
alter table public.rent_transactions
  alter column sgg_code  set not null,
  alter column umd_name  set not null,
  alter column area_kind set not null;

-- 동 이름 매핑에 실패한 거래도 버리지 않고 저장한 뒤, 나중에 다시 매핑한다
alter table public.rent_transactions
  alter column region_code drop not null;

alter table public.rent_transactions
  add constraint rent_transactions_area_kind_matches_type check (
    (housing_type = 'detached_multi_unit') = (area_kind = 'total_floor')
  ),
  add constraint rent_transactions_contract_months_first_day check (
    (contract_start_month is null or extract(day from contract_start_month) = 1)
    and (contract_end_month is null or extract(day from contract_end_month) = 1)
  ),
  add constraint rent_transactions_contract_term_order check (
    contract_start_month is null or contract_end_month is null
    or contract_start_month <= contract_end_month
  );
-- floor 는 smallint 그대로: 음수(반지하)를 허용한다

create index rent_transactions_unmapped_idx
  on public.rent_transactions (sgg_code, umd_name) where region_code is null;

-- regions: 시군구 코드 + 동 이름으로 법정동코드를 찾기 위한 유일 인덱스
create unique index regions_sigungu_dong_idx
  on public.regions (sigungu_code, dong_name);
