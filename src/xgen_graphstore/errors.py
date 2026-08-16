"""xgen-graphstore 예외 타입."""

from __future__ import annotations


class GraphStoreError(Exception):
    """graphstore 최상위 예외."""


class UnknownBackendError(GraphStoreError):
    """create_store 에 알 수 없는(등록 안 된) 백엔드 이름이 주어짐."""


class CapabilityError(GraphStoreError):
    """요청 연산이 현재 백엔드의 선언 능력 밖 — 무증상 오동작 대신 명확 차단(ADR-003)."""
