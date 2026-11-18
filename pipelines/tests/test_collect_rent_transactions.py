"""국토부 전월세 수집 테스트. fixture는 2026-11-19에 받은 성남 수정구 2026-10 실제 응답에서 골랐다."""

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines import collect_rent_transactions as collector
import json

from pipelines.clients.molit_client import MolitApiError, parse_rent_response_xml
from pipelines.clients.stan_regin_client import _parse_page
from pipelines.load_regions import parse_region_records, upsert_regions
from pipelines.parsers.molit_rent_parser import (
    parse_contract_term,
    parse_man_won,
    parse_rent_items,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_BY_TYPE = {
    "officetel": "molit_officetel_41131_202610.xml",
    "detached_multi_unit": "molit_sh_41131_202610.xml",
    "row_house": "molit_rh_41131_202610.xml",
}
REGION_CODE_BY_DONG = {("41131", "창곡동"): "4113110800", ("41131", "복정동"): "4113110700"}


def load_stan_regin_rows():
    rows, _ = _parse_page(json.loads((FIXTURES / "stan_regin_seongnam.json").read_text(encoding="utf-8")))
    return rows


def load_raw_items(housing_type):
    items, _ = parse_rent_response_xml((FIXTURES / FIXTURE_BY_TYPE[housing_type]).read_text(encoding="utf-8"))
    return items


# ---------- 값 파싱 ----------

def test_parse_man_won_removes_commas():
    assert parse_man_won("33,000") == 33000
    assert parse_man_won(" 95 ") == 95


def test_parse_man_won_rejects_blank():
    with pytest.raises(ValueError):
        parse_man_won(" ")


def test_parse_contract_term():
    assert parse_contract_term("26.11~27.10") == (date(2026, 11, 1), date(2027, 10, 1))
    assert parse_contract_term(" ") == (None, None)
    assert parse_contract_term("이상한값") == (None, None)


# ---------- 유형별 파싱 ----------

def test_officetel_record():
    result = parse_rent_items(load_raw_items("officetel"), "officetel", REGION_CODE_BY_DONG)
    first = result.records[0]
    assert result.skipped_count == 0
    assert first.region_code == "4113110800"         # 창곡동 매핑
    assert first.area_kind == "exclusive"
    assert first.area_m2 == Decimal("23.58")
    assert first.deposit_man_won == 1000 and first.monthly_rent_man_won == 95
    assert first.building_name == "위례엘포트한라비발디"
    assert first.contract_start_month == date(2026, 11, 1)
    assert first.contract_kind == "new" and first.is_renewal_right_used is None


def test_renewal_with_right_used():
    renewal = parse_rent_items(load_raw_items("officetel"), "officetel", {}).records[-1]
    assert renewal.contract_kind == "renewal"
    assert renewal.is_renewal_right_used is True
    assert (renewal.prev_deposit_man_won, renewal.prev_rent_man_won) == (1000, 75)


def test_multi_unit_uses_total_floor_area_and_blanks():
    records = parse_rent_items(load_raw_items("detached_multi_unit"), "detached_multi_unit", REGION_CODE_BY_DONG).records
    multi_unit = records[2]
    assert multi_unit.house_subtype == "multi_unit"
    assert multi_unit.area_kind == "total_floor"
    assert multi_unit.floor is None and multi_unit.jibun is None
    assert multi_unit.region_code == "4113110700"     # 복정동
    assert records[0].contract_kind is None            # 공백 계약유형
    assert records[-1].build_year is None              # 공백 건축년도


def test_row_house_semi_basement_and_combined_subtype():
    records = parse_rent_items(load_raw_items("row_house"), "row_house", {}).records
    assert records[0].floor == -1                      # 반지하
    assert records[0].region_code is None              # 매핑 실패도 버리지 않음
    assert records[-1].house_subtype == "row_or_multiplex"  # '연립다세대'


def test_identical_items_get_distinct_stable_hashes():
    item = load_raw_items("officetel")[0]
    first_run = parse_rent_items([item, dict(item)], "officetel", {}).records
    second_run = parse_rent_items([item, dict(item)], "officetel", {}).records
    assert first_run[0].source_hash != first_run[1].source_hash
    assert [r.source_hash for r in first_run] == [r.source_hash for r in second_run]


def test_broken_item_is_skipped_not_fatal():
    items = load_raw_items("officetel")
    broken = dict(items[0], deposit=" ")
    result = parse_rent_items([broken, items[1]], "officetel", {})
    assert result.skipped_count == 1 and len(result.records) == 1


def test_error_response_raises():
    error_xml = "<response><header><resultCode>30</resultCode><resultMsg>SERVICE KEY IS NOT REGISTERED</resultMsg></header></response>"
    with pytest.raises(MolitApiError):
        parse_rent_response_xml(error_xml)


# ---------- 기간 계산 ----------

def test_list_year_months_crosses_year():
    assert collector.list_year_months("202611", "202702") == ["202611", "202612", "202701", "202702"]


def test_default_range_is_last_three_months():
    assert collector.get_default_year_month_range(date(2027, 1, 15)) == ("202611", "202701")


# ---------- DB 통합 (멱등성) ----------

@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL 없음")
def test_collect_is_idempotent_and_remaps(monkeypatch):
    import psycopg

    monkeypatch.setattr(collector, "fetch_rent_items", lambda key, housing_type, sgg, ym: load_raw_items(housing_type))
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("delete from public.rent_transactions")
        connection.commit()

        def run_once():
            summary = collector.CollectionSummary()
            for housing_type in FIXTURE_BY_TYPE:
                collector.collect_unit(connection, "key", housing_type, "41131", "202610", {}, summary)
            return summary

        first = run_once()
        second = run_once()
        assert first.inserted_count == 17 and first.skipped_count == 0
        assert second.inserted_count == 0 and second.duplicate_count == 17   # 다시 돌려도 그대로

        # 처음엔 매핑 없이 넣었으니 전부 null → 성남 법정동을 넣고 재매핑
        upsert_regions(connection, parse_region_records(load_stan_regin_rows()))
        remapped_count = collector.remap_unmapped_regions(connection)
        with connection.cursor() as cursor:
            cursor.execute("select count(*) from public.rent_transactions where region_code is null")
            assert cursor.fetchone()[0] == 0
        assert remapped_count == 17


def test_api_failure_is_recorded_and_skipped(monkeypatch):
    def failing_fetch(*_):
        raise MolitApiError("timeout")

    monkeypatch.setattr(collector, "fetch_rent_items", failing_fetch)
    summary = collector.CollectionSummary()
    collector.collect_unit(None, "key", "officetel", "41131", "202610", {}, summary)
    assert len(summary.failed_units) == 1 and summary.inserted_count == 0
