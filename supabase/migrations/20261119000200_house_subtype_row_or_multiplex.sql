-- 003: 연립다세대 API의 houseType 에 '연립다세대' 값이 실제로 온다 (예: LH 행복주택 단지).
--      연립/다세대 어느 쪽인지 알 수 없으므로 별도 값으로 저장한다.
alter type public.house_subtype add value if not exists 'row_or_multiplex';
