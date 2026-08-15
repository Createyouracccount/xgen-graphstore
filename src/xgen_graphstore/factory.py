"""create_store — 백엔드 선택 팩토리 (요청된 범위: 유일 백엔드 fuseki).

라우터(dual-write·테넌트 분기)는 여기서 구현하지 않는다 — ADR-002 참조.
두 번째 백엔드(Neo4j, 0.2.0)가 생기면 그때 이 팩토리 위에 라우팅 층을 얹는다.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from xgen_graphstore.backend import FusekiBackend
from xgen_graphstore.errors import UnknownBackendError


def create_store(config: Optional[Dict[str, Any]] = None):
    """config 로 백엔드 구현을 선택해 반환.

    config = {
        "backend": "fuseki",         # 기본 fuseki (현재 유일)
        "base_url": ..., "dataset": ..., "username": ..., "password": ...,  # 선택
    }
    설정을 생략하면 백엔드가 env(FUSEKI_URL 등)에서 읽는다.
    미지 백엔드는 명시적 에러(조용한 fallback 금지).
    """
    config = dict(config or {})
    backend = (config.pop("backend", None) or "fuseki").lower()
    if backend == "fuseki":
        return FusekiBackend(
            base_url=config.get("base_url"),
            dataset=config.get("dataset"),
            username=config.get("username"),
            password=config.get("password"),
        )
    raise UnknownBackendError(
        f"알 수 없는 backend: {backend!r} (현재 지원: 'fuseki'). "
        f"Neo4j 백엔드는 0.2.0(3층)에서 추가 예정 — DEBTS.md 참조."
    )
