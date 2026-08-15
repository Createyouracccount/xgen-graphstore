"""xgen-graphstore 예외 타입."""

from __future__ import annotations


class GraphStoreError(Exception):
    """graphstore 최상위 예외."""


class UnknownBackendError(GraphStoreError):
    """create_store 에 알 수 없는 백엔드 이름이 주어짐."""
