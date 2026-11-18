\set ON_ERROR_STOP on
set search_path = public, extensions;
-- 01_behavior_tests.sql 이후 실행 (regions 4113110700 복정동 존재)
-- 오피스텔: 전용면적, 정상
insert into rent_transactions (source_hash,region_code,sgg_code,umd_name,housing_type,area_kind,area_m2,floor,build_year,deposit_man_won,monthly_rent_man_won,contract_date,contract_kind,building_name,contract_start_month,contract_end_month)
values ('h1','4113110700','41131','복정동','officetel','exclusive',23.58,18,2020,1000,95,'2026-10-12','new','위례엘포트','2026-11-01','2027-10-01');
-- 다세대 반지하: floor -1 허용
insert into rent_transactions (source_hash,region_code,sgg_code,umd_name,housing_type,house_subtype,area_kind,area_m2,floor,deposit_man_won,monthly_rent_man_won,contract_date,contract_kind,prev_deposit_man_won)
values ('h2','4113110700','41131','복정동','row_house','multiplex','exclusive',59.88,-1,18000,0,'2026-10-17','renewal',15000);
-- 다가구: 연면적, 매핑 실패(region_code null)도 저장 가능
insert into rent_transactions (source_hash,sgg_code,umd_name,housing_type,house_subtype,area_kind,area_m2,deposit_man_won,monthly_rent_man_won,contract_date)
values ('h3','41131','수진동','detached_multi_unit','multi_unit','total_floor',37,200,35,'2026-10-28');
select count(*) as unmapped_should_be_1 from rent_transactions where region_code is null;
-- 다가구인데 전용면적이라고 표시 → 실패
do $$ begin
  insert into rent_transactions (source_hash,sgg_code,umd_name,housing_type,area_kind,area_m2,deposit_man_won,monthly_rent_man_won,contract_date)
  values ('h4','41131','신흥동','detached_multi_unit','exclusive',35,200,35,'2026-10-28');
  raise exception 'FAIL';
exception when check_violation then raise notice 'OK: area_kind must match housing_type'; end $$;
-- 같은 source_hash 재수집 → upsert로 멱등
insert into rent_transactions (source_hash,sgg_code,umd_name,housing_type,house_subtype,area_kind,area_m2,deposit_man_won,monthly_rent_man_won,contract_date)
values ('h3','41131','수진동','detached_multi_unit','multi_unit','total_floor',37,200,35,'2026-10-28')
on conflict (source_hash) do nothing;
select count(*) as total_should_be_3 from rent_transactions;
-- 동 이름 중복 등록 → 실패
do $$ begin
  insert into regions (region_code,sigungu_code,sido_name,sigungu_name,dong_name) values ('4113199999','41131','경기도','성남시 수정구','복정동');
  raise exception 'FAIL';
exception when unique_violation then raise notice 'OK: (sigungu_code, dong_name) unique'; end $$;
\echo RENT TRANSACTION TESTS PASSED
