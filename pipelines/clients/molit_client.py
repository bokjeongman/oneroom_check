"""국토부 전월세 실거래가 API 클라이언트 — 호출, 재시도, 페이지네이션, XML → 원시 dict 변환만 한다.

값 해석(콤마 제거, 빈 값 처리 등)은 하지 않는다. 그건 parsers/molit_rent_parser.py 의 일이다.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ElementTree

import requests

MOLIT_BASE_URL = "https://apis.data.go.kr/1613000"
# housing_type(DB enum) → API 경로
MOLIT_RENT_PATHS = {
    "detached_multi_unit": "RTMSDataSvcSHRent/getRTMSDataSvcSHRent",
    "row_house": "RTMSDataSvcRHRent/getRTMSDataSvcRHRent",
    "officetel": "RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent",
}
PAGE_SIZE = 1000
REQUEST_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3.0
SUCCESS_RESULT_CODES = {"000", "00"}


class MolitApiError(RuntimeError):
    """API가 정상 응답 코드를 주지 않았을 때."""


def fetch_rent_items(service_key: str, housing_type: str, sgg_code: str, deal_year_month: str) -> list[dict[str, str]]:
    """한 시군구·한 달·한 주택유형의 전월세 거래를 모두 가져온다. 각 item은 {태그명: 원문 문자열}.

    실패 시 동작: 재시도 후에도 실패하면 예외 (해당 단위만 실패로 기록하고 배치는 다음 단위로 진행).
    """
    path = MOLIT_RENT_PATHS[housing_type]
    items: list[dict[str, str]] = []
    page_number = 1
    while True:
        xml_text = _fetch_page_with_retry(service_key, path, sgg_code, deal_year_month, page_number)
        page_items, total_count = parse_rent_response_xml(xml_text)
        items.extend(page_items)
        if len(items) >= total_count or not page_items:
            return items
        page_number += 1


def _fetch_page_with_retry(service_key: str, path: str, sgg_code: str, deal_year_month: str, page_number: int) -> str:
    params = {
        "serviceKey": service_key,
        "LAWD_CD": sgg_code,
        "DEAL_YMD": deal_year_month,
        "numOfRows": PAGE_SIZE,
        "pageNo": page_number,
    }
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(f"{MOLIT_BASE_URL}/{path}", params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.text
        except requests.RequestException as error:
            last_error = error
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise MolitApiError(f"국토부 API 호출 실패: {path} {sgg_code} {deal_year_month} p{page_number}") from last_error


def parse_rent_response_xml(xml_text: str) -> tuple[list[dict[str, str]], int]:
    """응답 XML → (item 목록, 전체 건수). 결과 코드가 정상이 아니면 예외."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise MolitApiError(f"XML 파싱 실패: {xml_text[:200]}") from error
    result_code = (root.findtext("./header/resultCode") or "").strip()
    if result_code not in SUCCESS_RESULT_CODES:
        result_message = root.findtext("./header/resultMsg") or root.findtext(".//returnAuthMsg") or ""
        raise MolitApiError(f"국토부 API 오류 {result_code}: {result_message}")
    items = [{child.tag: (child.text or "") for child in item} for item in root.iterfind(".//items/item")]
    total_count = int(root.findtext("./body/totalCount") or 0)
    return items, total_count
