-- 004: Supabase는 public 스키마의 새 함수에 anon/authenticated 실행 권한을 기본으로 준다.
--      001에서 public 에서만 revoke 해서 실제로는 막히지 않았다 (보안 점검에서 발견).
--      의도한 호출자만 남긴다.

-- 서버 전용: 사용량 카운터 (anon이 호출하면 남의 한도를 소진시킬 수 있음)
revoke execute on function public.try_increment_capture_usage(text, integer) from anon, authenticated;
-- 트리거 전용: 직접 호출할 이유가 없다
revoke execute on function public.create_profile_for_new_user() from anon, authenticated;
-- 로그인 사용자 전용
revoke execute on function public.claim_anonymous_judgments(uuid) from anon;
-- get_shared_judgment 는 공유 링크용으로 anon 허용 (의도)

-- 트리거 함수 search_path 고정
alter function public.set_updated_at() set search_path = '';
