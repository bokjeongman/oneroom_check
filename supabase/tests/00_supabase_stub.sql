-- 로컬 검증용: Supabase 환경 흉내 (실제 Supabase에는 이미 있음)
create schema if not exists extensions;
create schema if not exists auth;
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
end $$;
create table auth.users (id uuid primary key, email text, raw_app_meta_data jsonb default '{}', raw_user_meta_data jsonb default '{}');
create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
grant usage on schema public, auth, extensions to anon, authenticated, service_role;
grant execute on function auth.uid() to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
-- Supabase 실제 동작: 새 함수 실행 권한이 anon/authenticated에도 기본 부여된다
alter default privileges in schema public grant execute on functions to anon, authenticated, service_role;
alter database postgres set search_path = public, extensions;
