"""국토부 전월세 API 원시 item → RentTransactionRecord 변환. 순수 함수만 둔다 (네트워크·DB 없음).

실측으로 확인한 응답 특성 (2026-11-19):
- 금액에 콤마가 있다: "33,000"
- 빈 값은 공백 한 칸 " " 으로 온다
- 단독/다가구: 전용면적·층·지번이 없고 totalFloorAr(연면적)만, buildYear가 비기도 한다
- 연립다세대: floor = -1 은 반지하, houseType 에 '연립다세대' 값도 있다
- 계약기간 "26.11~27.10", 갱신요구권 "사용" 또는 공백
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

CONTRACT_KINDS = {"신규": "new", "갱신": "renewal"}
HOUSE_SUBTYPES = {
    "단독": "detached",
    "다가구": "multi_unit",
    "연립": "row_house",
    "다세대": "multiplex",
    "연립다세대": "row_or_multiplex",
}
RENEWAL_RIGHT_USED_LABEL = "사용"
TWO_DIGIT_YEAR_BASE = 2000


@dataclass(frozen=True)
class RentTransactionRecord:
    source_hash: str
    region_code: str | None
    sgg_code: str
    umd_name: str
    housing_type: str
    house_subtype: str | None
    area_kind: str
    area_m2: Decimal
    floor: int | None
    build_year: int | None
    jibun: str | None
    building_name: str | None
    deposit_man_won: int
    monthly_rent_man_won: int
    contract_date: date
    contract_kind: str | None
    prev_deposit_man_won: int | None
    prev_rent_man_won: int | None
    contract_start_month: date | None
    contract_end_month: date | None
    is_renewal_right_used: bool | None

    def to_row(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RentParseResult:
    records: list[RentTransactionRecord]
    skipped_count: int  # 필수 값이 비었거나 형식이 깨진 item 수 (버리고 진행)


def parse_rent_items(
    raw_items: list[dict[str, str]],
    housing_type: str,
    region_code_by_dong: dict[tuple[str, str], str],
) -> RentParseResult:
    """한 번의 조회 결과(같은 유형·시군구·월)를 레코드로 바꾼다.

    완전히 같은 거래가 여러 건이면(같은 건물·조건·날짜) 순번을 해시에 넣어 서로 다른 행으로 남긴다.
    API는 같은 조회에 같은 순서·같은 결과를 주므로 재실행해도 해시가 같다(멱등).
    """
    occurrence_by_key: dict[str, int] = {}
    records = []
    skipped_count = 0
    for raw_item in raw_items:
        natural_key = _build_natural_key(raw_item, housing_type)
        occurrence = occurrence_by_key.get(natural_key, 0)
        occurrence_by_key[natural_key] = occurrence + 1
        try:
            records.append(_parse_rent_item(raw_item, housing_type, natural_key, occurrence, region_code_by_dong))
        except (KeyError, ValueError, InvalidOperation):
            skipped_count += 1
    return RentParseResult(records=records, skipped_count=skipped_count)


def _parse_rent_item(
    raw_item: dict[str, str],
    housing_type: str,
    natural_key: str,
    occurrence: int,
    region_code_by_dong: dict[tuple[str, str], str],
) -> RentTransactionRecord:
    sgg_code = _clean(raw_item.get("sggCd"))
    umd_name = _clean(raw_item.get("umdNm"))
    is_total_floor_area = housing_type == "detached_multi_unit"
    contract_start_month, contract_end_month = parse_contract_term(raw_item.get("contractTerm"))
    return RentTransactionRecord(
        source_hash=_calculate_source_hash(natural_key, occurrence),
        region_code=region_code_by_dong.get((sgg_code, umd_name)),
        sgg_code=sgg_code,
        umd_name=umd_name,
        housing_type=housing_type,
        house_subtype=HOUSE_SUBTYPES.get(_clean(raw_item.get("houseType"))),
        area_kind="total_floor" if is_total_floor_area else "exclusive",
        area_m2=Decimal(_clean(raw_item.get("totalFloorAr" if is_total_floor_area else "excluUseAr"))),
        floor=parse_optional_int(raw_item.get("floor")),
        build_year=parse_optional_int(raw_item.get("buildYear")),
        jibun=_clean(raw_item.get("jibun")) or None,
        building_name=_clean(raw_item.get("offiNm") or raw_item.get("mhouseNm")) or None,
        deposit_man_won=parse_man_won(raw_item["deposit"]),
        monthly_rent_man_won=parse_man_won(raw_item["monthlyRent"]),
        contract_date=date(int(raw_item["dealYear"]), int(raw_item["dealMonth"]), int(raw_item["dealDay"])),
        contract_kind=CONTRACT_KINDS.get(_clean(raw_item.get("contractType"))),
        prev_deposit_man_won=parse_optional_man_won(raw_item.get("preDeposit")),
        prev_rent_man_won=parse_optional_man_won(raw_item.get("preMonthlyRent")),
        contract_start_month=contract_start_month,
        contract_end_month=contract_end_month,
        is_renewal_right_used=_parse_renewal_right(raw_item.get("useRRRight"), raw_item.get("contractType")),
    )


def parse_man_won(raw_value: str) -> int:
    """'33,000' → 33000. 빈 값이면 예외 (보증금·월세는 필수)."""
    cleaned = _clean(raw_value).replace(",", "")
    if not cleaned:
        raise ValueError("필수 금액 값이 비어 있습니다")
    return int(cleaned)


def parse_optional_man_won(raw_value: str | None) -> int | None:
    cleaned = _clean(raw_value).replace(",", "")
    return int(cleaned) if cleaned else None


def parse_optional_int(raw_value: str | None) -> int | None:
    cleaned = _clean(raw_value)
    return int(cleaned) if cleaned else None


def parse_contract_term(raw_value: str | None) -> tuple[date | None, date | None]:
    """'26.11~27.10' → (2026-11-01, 2027-10-01). 비었거나 형식이 다르면 (None, None)."""
    cleaned = _clean(raw_value)
    if "~" not in cleaned:
        return None, None
    try:
        start_text, end_text = cleaned.split("~")
        return _parse_year_month(start_text), _parse_year_month(end_text)
    except ValueError:
        return None, None


def _parse_year_month(text: str) -> date:
    two_digit_year, month = text.strip().split(".")
    return date(TWO_DIGIT_YEAR_BASE + int(two_digit_year), int(month), 1)


def _parse_renewal_right(raw_value: str | None, raw_contract_type: str | None) -> bool | None:
    """갱신 계약일 때만 의미가 있다: '사용' → True, 공백 → False. 신규·미상이면 None."""
    if CONTRACT_KINDS.get(_clean(raw_contract_type)) != "renewal":
        return None
    return _clean(raw_value) == RENEWAL_RIGHT_USED_LABEL


def _build_natural_key(raw_item: dict[str, str], housing_type: str) -> str:
    fields = [housing_type] + [
        _clean(raw_item.get(tag))
        for tag in (
            "sggCd", "umdNm", "jibun", "offiNm", "mhouseNm", "houseType", "excluUseAr", "totalFloorAr",
            "floor", "buildYear", "dealYear", "dealMonth", "dealDay", "deposit", "monthlyRent",
            "contractType", "contractTerm", "preDeposit", "preMonthlyRent",
        )
    ]
    return "|".join(fields)


def _calculate_source_hash(natural_key: str, occurrence: int) -> str:
    return hashlib.sha256(f"{natural_key}#{occurrence}".encode("utf-8")).hexdigest()


def _clean(raw_value: str | None) -> str:
    return (raw_value or "").strip()
