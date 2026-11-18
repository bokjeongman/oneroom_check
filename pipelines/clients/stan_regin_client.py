"""행정안전부 법정동코드 API 클라이언트 — 호출, 재시도, 페이지네이션만 담당한다."""

from __future__ import annotations

import time

import requests

STAN_REGIN_URL = "https://apis.data.go.kr/1741000/StanReginCd/getStanReginCdList"
PAGE_SIZE = 1000
REQUEST_TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0
SUCCESS_RESULT_CODE = "INFO-0"


class StanReginApiError(RuntimeError):
    """API가 정상 응답 코드를 주지 않았을 때."""


def fetch_legal_dong_rows(service_key: str, address_prefix: str) -> list[dict]:
    """주소 접두어(예: '경기도 성남시')에 속한 현존 법정동 행을 모두 가져온다.

    실패 시 동작: 재시도 후에도 실패하면 예외를 던진다 (배치가 실패로 끝나고 기존 regions는 그대로).
    """
    rows: list[dict] = []
    page_number = 1
    while True:
        response_body = _fetch_page_with_retry(service_key, address_prefix, page_number)
        page_rows, total_count = _parse_page(response_body)
        rows.extend(page_rows)
        if len(rows) >= total_count or not page_rows:
            return rows
        page_number += 1


def _fetch_page_with_retry(service_key: str, address_prefix: str, page_number: int) -> dict:
    params = {
        "serviceKey": service_key,
        "type": "json",
        "pageNo": page_number,
        "numOfRows": PAGE_SIZE,
        "flag": "Y",  # 현존 법정동만
        "locatadd_nm": address_prefix,
    }
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(STAN_REGIN_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            last_error = error
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise StanReginApiError(f"법정동코드 API 호출 실패 (page {page_number})") from last_error


def _parse_page(response_body: dict) -> tuple[list[dict], int]:
    """응답 JSON에서 (행 목록, 전체 건수)를 꺼낸다. 비정상 응답이면 예외."""
    try:
        head, body = response_body["StanReginCd"]
        result_code = head["head"][2]["RESULT"]["resultCode"]
        total_count = int(head["head"][0]["totalCount"])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise StanReginApiError(f"예상하지 못한 응답 형식: {str(response_body)[:200]}") from error
    if result_code != SUCCESS_RESULT_CODE:
        raise StanReginApiError(f"API 오류 코드: {result_code}")
    return body.get("row", []), total_count
