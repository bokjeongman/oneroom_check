"""배치 단계: 국토부 전월세 API → rent_transactions 적재 (멱등).

실행 예:
  python -m pipelines.collect_rent_transactions                          # 성남 3개 구, 최근 3개월
  python -m pipelines.collect_rent_transactions --from 202106 --to 202610 # 백필
필요한 환경변수: DATA_GO_KR_SERVICE_KEY (Decoding 키), DATABASE_URL

신고 기한(계약 후 30일) 때문에 지난달 거래가 늦게 들어오므로, 매 실행마다 최근 몇 달을 다시 긁는다.
같은 거래는 source_hash 로 걸러지므로 다시 긁어도 중복되지 않는다.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import date

from pipelines.clients.db_client import connect_to_database
from pipelines.clients.molit_client import MOLIT_RENT_PATHS, MolitApiError, fetch_rent_items
from pipelines.parsers.molit_rent_parser import RentTransactionRecord, parse_rent_items

SERVICE_KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
SEONGNAM_SGG_CODES = ("41131", "41133", "41135")  # 수정구, 중원구, 분당구
DEFAULT_LOOKBACK_MONTHS = 3


@dataclass
class CollectionSummary:
    inserted_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0
    failed_units: list[str] = field(default_factory=list)


def list_year_months(from_year_month: str, to_year_month: str) -> list[str]:
    """'202611', '202701' → ['202611', '202612', '202701']"""
    year, month = int(from_year_month[:4]), int(from_year_month[4:])
    end_year, end_month = int(to_year_month[:4]), int(to_year_month[4:])
    year_months = []
    while (year, month) <= (end_year, end_month):
        year_months.append(f"{year}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return year_months


def get_default_year_month_range(today: date) -> tuple[str, str]:
    """오늘 기준 최근 DEFAULT_LOOKBACK_MONTHS 개월 (이번 달 포함)."""
    year, month = today.year, today.month
    for _ in range(DEFAULT_LOOKBACK_MONTHS - 1):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return f"{year}{month:02d}", f"{today.year}{today.month:02d}"


def fetch_region_code_by_dong(connection) -> dict[tuple[str, str], str]:
    with connection.cursor() as cursor:
        cursor.execute("select sigungu_code, dong_name, region_code from public.regions")
        return {(sigungu_code, dong_name): region_code for sigungu_code, dong_name, region_code in cursor.fetchall()}


INSERT_RENT_TRANSACTION_SQL = """
insert into public.rent_transactions (
  source_hash, region_code, sgg_code, umd_name, housing_type, house_subtype, area_kind, area_m2,
  floor, build_year, jibun, building_name, deposit_man_won, monthly_rent_man_won, contract_date,
  contract_kind, prev_deposit_man_won, prev_rent_man_won, contract_start_month, contract_end_month,
  is_renewal_right_used
) values (
  %(source_hash)s, %(region_code)s, %(sgg_code)s, %(umd_name)s, %(housing_type)s, %(house_subtype)s,
  %(area_kind)s, %(area_m2)s, %(floor)s, %(build_year)s, %(jibun)s, %(building_name)s,
  %(deposit_man_won)s, %(monthly_rent_man_won)s, %(contract_date)s, %(contract_kind)s,
  %(prev_deposit_man_won)s, %(prev_rent_man_won)s, %(contract_start_month)s, %(contract_end_month)s,
  %(is_renewal_right_used)s
)
on conflict (source_hash) do nothing
"""


def insert_rent_transactions(connection, records: list[RentTransactionRecord]) -> int:
    """새 거래만 넣는다. 반환: 실제로 들어간 건수."""
    inserted_count = 0
    with connection.cursor() as cursor:
        for record in records:
            cursor.execute(INSERT_RENT_TRANSACTION_SQL, record.to_row())
            inserted_count += cursor.rowcount
    connection.commit()
    return inserted_count


REMAP_UNMAPPED_REGIONS_SQL = """
update public.rent_transactions t
   set region_code = r.region_code
  from public.regions r
 where t.region_code is null
   and r.sigungu_code = t.sgg_code
   and r.dong_name = t.umd_name
"""


def remap_unmapped_regions(connection) -> int:
    """예전에 매핑 못 한 거래를 regions 기준으로 다시 매핑한다. 반환: 매핑된 건수."""
    with connection.cursor() as cursor:
        cursor.execute(REMAP_UNMAPPED_REGIONS_SQL)
        remapped_count = cursor.rowcount
    connection.commit()
    return remapped_count


def collect_unit(connection, service_key, housing_type, sgg_code, year_month, region_code_by_dong, summary) -> None:
    """한 단위(유형 × 시군구 × 월)를 수집한다. API 실패는 기록만 하고 다음 단위로 넘어간다."""
    unit_label = f"{housing_type}/{sgg_code}/{year_month}"
    try:
        raw_items = fetch_rent_items(service_key, housing_type, sgg_code, year_month)
    except MolitApiError as error:
        summary.failed_units.append(f"{unit_label}: {error}")
        return
    parse_result = parse_rent_items(raw_items, housing_type, region_code_by_dong)
    inserted_count = insert_rent_transactions(connection, parse_result.records)
    summary.inserted_count += inserted_count
    summary.duplicate_count += len(parse_result.records) - inserted_count
    summary.skipped_count += parse_result.skipped_count
    print(f"{unit_label}: 응답 {len(raw_items)} / 신규 {inserted_count} / 건너뜀 {parse_result.skipped_count}")


def main(sgg_codes: list[str], from_year_month: str, to_year_month: str) -> int:
    service_key = os.environ[SERVICE_KEY_ENV]
    summary = CollectionSummary()
    with connect_to_database() as connection:
        region_code_by_dong = fetch_region_code_by_dong(connection)
        for year_month in list_year_months(from_year_month, to_year_month):
            for sgg_code in sgg_codes:
                for housing_type in MOLIT_RENT_PATHS:
                    collect_unit(connection, service_key, housing_type, sgg_code, year_month, region_code_by_dong, summary)
        remapped_count = remap_unmapped_regions(connection)
    print(
        f"완료: 신규 {summary.inserted_count}, 중복 {summary.duplicate_count}, "
        f"건너뜀 {summary.skipped_count}, 재매핑 {remapped_count}, 실패 단위 {len(summary.failed_units)}"
    )
    for failed_unit in summary.failed_units:
        print(f"  실패: {failed_unit}", file=sys.stderr)
    return 1 if summary.failed_units else 0


def _parse_arguments() -> argparse.Namespace:
    default_from, default_to = get_default_year_month_range(date.today())
    parser = argparse.ArgumentParser(description="국토부 전월세 실거래 수집")
    parser.add_argument("--sgg-codes", default=",".join(SEONGNAM_SGG_CODES))
    parser.add_argument("--from", dest="from_year_month", default=default_from)
    parser.add_argument("--to", dest="to_year_month", default=default_to)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_arguments()
    sys.exit(main(arguments.sgg_codes.split(","), arguments.from_year_month, arguments.to_year_month))
