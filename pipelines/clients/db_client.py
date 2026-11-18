"""Postgres(Supabase) 연결 — 배치 전용. 연결만 담당한다."""

from __future__ import annotations

import os

import psycopg

DATABASE_URL_ENV = "DATABASE_URL"  # Supabase > Project Settings > Database > Connection string


def connect_to_database() -> psycopg.Connection:
    database_url = os.environ.get(DATABASE_URL_ENV)
    if not database_url:
        raise RuntimeError(f"환경변수 {DATABASE_URL_ENV} 가 없습니다")
    return psycopg.connect(database_url, autocommit=False)
