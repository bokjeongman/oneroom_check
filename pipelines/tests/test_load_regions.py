"""load_regions 단위 테스트 + 로컬 Postgres 멱등성 테스트.

실제 API 응답(2026-11-19 성남시)을 저장한 fixture를 쓴다.
멱등성 테스트는 TEST_DATABASE_URL 이 있을 때만 돈다 (마이그레이션 적용된 DB).
"""

import json
import os
from pathlib import Path

import pytest

from pipelines.clients.stan_regin_client import StanReginApiError, _parse_page
from pipelines.load_regions import parse_region_records, upsert_regions

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "stan_regin_seongnam.json"
# 국토부 전월세 응답(2026-10 수정구)에 실제로 나온 동 이름
DONG_NAMES_SEEN_IN_MOLIT = {"창곡동", "신흥동", "단대동", "수진동"}


@pytest.fixture
def api_rows():
    rows, _ = _parse_page(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
    return rows


def test_keeps_only_dong_level_rows(api_rows):
    records = parse_region_records(api_rows)
    assert len(api_rows) == 48          # 시 1 + 구 3 + 법정동 44
    assert len(records) == 44
    assert all(not r.region_code.endswith("00000") for r in records)


def test_splits_address_into_names(api_rows):
    bokjeong = next(r for r in parse_region_records(api_rows) if r.dong_name == "복정동")
    assert bokjeong.region_code == "4113110700"
    assert bokjeong.sigungu_code == "41131"
    assert bokjeong.sido_name == "경기도"
    assert bokjeong.sigungu_name == "성남시 수정구"


def test_covers_dong_names_returned_by_molit(api_rows):
    dong_names = {r.dong_name for r in parse_region_records(api_rows) if r.sigungu_code == "41131"}
    assert DONG_NAMES_SEEN_IN_MOLIT <= dong_names


def test_dong_name_unique_within_sigungu(api_rows):
    keys = [(r.sigungu_code, r.dong_name) for r in parse_region_records(api_rows)]
    assert len(keys) == len(set(keys))


def test_error_result_code_raises():
    body = {"StanReginCd": [{"head": [{"totalCount": 0}, {}, {"RESULT": {"resultCode": "ERROR-300"}}]}, {}]}
    with pytest.raises(StanReginApiError):
        _parse_page(body)


def test_unexpected_shape_raises():
    with pytest.raises(StanReginApiError):
        _parse_page({"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"errMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}}})


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL 없음")
def test_upsert_is_idempotent(api_rows):
    import psycopg

    records = parse_region_records(api_rows)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        upsert_regions(connection, records)
        upsert_regions(connection, records)  # 두 번 실행
        with connection.cursor() as cursor:
            cursor.execute("select count(*) from public.regions where sigungu_code like '4113%'")
            assert cursor.fetchone()[0] == 44
