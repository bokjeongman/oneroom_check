\set ON_ERROR_STOP on
set search_path = public, extensions;
-- 기본 데이터
insert into regions values ('4113110700','41131','경기도','성남시 수정구','복정동', null, ST_SetSRID(ST_MakePoint(127.13,37.46),4326));
insert into auth.users (id,email,raw_app_meta_data,raw_user_meta_data) values
 ('11111111-1111-1111-1111-111111111111','a@x.com','{"provider":"kakao"}','{"name":"태규"}'),
 ('22222222-2222-2222-2222-222222222222','b@x.com','{"provider":"google"}','{"full_name":"B"}');
select user_id, display_name, auth_provider from profiles order by 1;  -- 트리거로 2건
insert into model_versions (version_tag,train_period_start,train_period_end,test_mape,artifact_uri,is_active)
  values ('rent-lgbm-2026-11-19','2021-06-01','2026-10-31',12.3,'gs://m/v1.txt',true);
-- 활성 모델 2개 → 실패해야 함
do $$ begin
  insert into model_versions (version_tag,train_period_start,train_period_end,artifact_uri,is_active) values ('v2','2021-06-01','2026-10-31','x',true);
  raise exception 'FAIL: two active models allowed';
exception when unique_violation then raise notice 'OK: single active model enforced'; end $$;
-- 판정: 비로그인 1, 사용자1 1, 사용자2 1
insert into judgments (id,anonymous_id,mode,input_source,region_code,housing_type,area_m2,deposit_man_won,monthly_rent_man_won,predicted_rent_man_won,predicted_lower_man_won,predicted_upper_man_won,verdict,judged_by,model_version_id) values
 ('aaaaaaaa-0000-0000-0000-000000000001','99999999-9999-9999-9999-999999999999','quick_check','manual','4113110700','detached_multi_unit',19.8,500,50,45,40,50,'slightly_high','model',1);
insert into judgments (user_id,mode,input_source,region_code,housing_type,area_m2,deposit_man_won,monthly_rent_man_won,predicted_rent_man_won,predicted_lower_man_won,predicted_upper_man_won,verdict,judged_by) values
 ('22222222-2222-2222-2222-222222222222','renewal_check','manual','4113110700','row_house',30,1000,40,42,38,46,'fair','stats_fallback');
-- 모델 판정인데 모델 버전 없음 → 실패
do $$ begin
  insert into judgments (anonymous_id,mode,input_source,region_code,housing_type,area_m2,deposit_man_won,monthly_rent_man_won,predicted_rent_man_won,predicted_lower_man_won,predicted_upper_man_won,verdict,judged_by)
  values (gen_random_uuid(),'quick_check','manual','4113110700','officetel',20,500,50,45,40,50,'fair','model');
  raise exception 'FAIL';
exception when check_violation then raise notice 'OK: model judgment requires model_version'; end $$;
-- 구독 CHECK: renewal_watch 인데 계약정보 없음 → 실패
do $$ begin
  insert into subscriptions (user_id,kind,region_code,housing_type,area_bucket) values ('11111111-1111-1111-1111-111111111111','renewal_watch','4113110700','row_house','under_20m2');
  raise exception 'FAIL';
exception when check_violation then raise notice 'OK: renewal_watch requires contract info'; end $$;
-- 사용량 한도 3
select try_increment_capture_usage('ip:abc',3) as r1, try_increment_capture_usage('ip:abc',3) as r2,
       try_increment_capture_usage('ip:abc',3) as r3, try_increment_capture_usage('ip:abc',3) as r4_should_be_false;

-- === RLS: 사용자1로 ===
set role authenticated;
select set_config('request.jwt.claim.sub','11111111-1111-1111-1111-111111111111',false);
select count(*) as user1_sees_judgments_should_be_0 from judgments;
select count(*) as user1_sees_profiles_should_be_1 from profiles;
select verdict as shared_lookup_works from get_shared_judgment('aaaaaaaa-0000-0000-0000-000000000001');
select claim_anonymous_judgments('99999999-9999-9999-9999-999999999999') as claimed_should_be_1;
select count(*) as user1_sees_after_claim_should_be_1 from judgments;
insert into subscriptions (user_id,kind,region_code,housing_type,area_bucket) values ('11111111-1111-1111-1111-111111111111','area_watch','4113110700','detached_multi_unit','under_20m2');
do $$ begin
  insert into subscriptions (user_id,kind,region_code,housing_type,area_bucket) values ('22222222-2222-2222-2222-222222222222','area_watch','4113110700','row_house','under_20m2');
  raise exception 'FAIL: inserted for other user';
exception when insufficient_privilege then raise notice 'OK: cannot subscribe for another user'; end $$;
do $$ begin
  insert into judgments (user_id,mode,input_source,region_code,housing_type,area_m2,deposit_man_won,monthly_rent_man_won,predicted_rent_man_won,predicted_lower_man_won,predicted_upper_man_won,verdict,judged_by)
  values ('11111111-1111-1111-1111-111111111111','quick_check','manual','4113110700','officetel',20,500,50,45,40,50,'fair','stats_fallback');
  raise exception 'FAIL: client wrote judgment';
exception when insufficient_privilege then raise notice 'OK: judgments writable by server only'; end $$;
do $$ begin
  perform try_increment_capture_usage('x',3);
  raise exception 'FAIL';
exception when insufficient_privilege then raise notice 'OK: usage fn server only'; end $$;
select count(*) as stats_public_read_ok from region_rent_stats;
reset role;
-- === anon ===
set role anon;
select set_config('request.jwt.claim.sub','',false);
do $$ begin
  perform try_increment_capture_usage('user:victim',3);
  raise exception 'FAIL: anon can burn usage';
exception when insufficient_privilege then raise notice 'OK: anon cannot call usage fn'; end $$;
do $$ begin
  perform claim_anonymous_judgments(gen_random_uuid());
  raise exception 'FAIL: anon can claim';
exception when insufficient_privilege then raise notice 'OK: anon cannot claim judgments'; end $$;
select count(*) as anon_sees_judgments_should_be_0 from judgments;
select count(*) as anon_sees_regions_should_be_1 from regions;
select verdict as anon_shared_lookup from get_shared_judgment('aaaaaaaa-0000-0000-0000-000000000001');
reset role;
\echo ALL TESTS PASSED
