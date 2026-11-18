"""배치 단계: 법정동코드 API → regions 테이블 적재 (멱등).

실행: python -m pipelines.load_regions "경기도 성남시"
필요한 환경변수: DATA_GO_KR_SERVICE_KEY (Decoding 키), DATABASE_URL
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from pipelines.clients.db_client import connect_to_database
from pipelines.clients.stan_regin_client import fetch_legal_dong_rows

SERVICE_KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
DEFAULT_ADDRESS_PREFIX = "경기도 성남시"
SIGUNGU_LEVEL_UMD_CODE = "000"  # 시·구 자체를 나타내는 행
NO_RI_CODE = "00"


@dataclass(frozen=True)
class RegionRecord:
    region_code: str
    sigungu_code: str
    sido_name: str
    sigungu_name: str
    dong_name: str


def parse_region_records(api_rows: list[dict]) -> list[RegionRecord]:
    """API 행에서 법정동(읍면동) 행만 골라 RegionRecord로 바꾼다. 시·구·리 행은 버린다."""
    records = []
    for row in api_rows:
        if row["umd_cd"] == SIGUNGU_LEVEL_UMD_CODE or row["ri_cd"] != NO_RI_CODE:
            continue
        records.append(_to_region_record(row))
    return records


def _to_region_record(row: dict) -> RegionRecord:
    # 예: "경기도 성남시 수정구 복정동" → 시도 "경기도", 시군구 "성남시 수정구", 동 "복정동"
    address_parts = row["locatadd_nm"].split()
    return RegionRecord(
        region_code=row["region_cd"],
        sigungu_code=row["region_cd"][:5],
        sido_name=address_parts[0],
        sigungu_name=" ".join(address_parts[1:-1]),
        dong_name=address_parts[-1],
    )


UPSERT_REGION_SQL = """
insert into public.regions (region_code, sigungu_code, sido_name, sigungu_name, dong_name)
values (%(region_code)s, %(sigungu_code)s, %(sido_name)s, %(sigungu_name)s, %(dong_name)s)
on conflict (region_code) do update
   set sigungu_code = excluded.sigungu_code,
       sido_name    = excluded.sido_name,
       sigungu_name = excluded.sigungu_name,
       dong_name    = excluded.dong_name
"""


def upsert_regions(connection, records: list[RegionRecord]) -> int:
    """regions에 upsert한다. 경계(boundary)·중심점은 건드리지 않는다. 반환: 처리 건수."""
    with connection.cursor() as cursor:
        cursor.executemany(UPSERT_REGION_SQL, [record.__dict__ for record in records])
    connection.commit()
    return len(records)


def main(address_prefix: str) -> None:
    service_key = os.environ[SERVICE_KEY_ENV]
    api_rows = fetch_legal_dong_rows(service_key, address_prefix)
    records = parse_region_records(api_rows)
    with connect_to_database() as connection:
        upserted_count = upsert_regions(connection, records)
    print(f"regions upsert: {upserted_count}건 ({address_prefix})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ADDRESS_PREFIX)
