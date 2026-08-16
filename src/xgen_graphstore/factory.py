"""create_store — 백엔드 선택 팩토리 (요청된 범위: 유일 백엔드 fuseki).

라우터(dual-write·테넌트 분기)는 여기서 구현하지 않는다 — ADR-002 참조.
두 번째 백엔드(Neo4j, 0.2.0)가 생기면 그때 이 팩토리 위에 라우팅 층을 얹는다.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from xgen_graphstore.backend import FusekiBackend
from xgen_graphstore.errors import UnknownBackendError


def create_store(config: Optional[Dict[str, Any]] = None):
    """config 로 백엔드 구현을 선택해 반환.

    config = {
        "backend": "fuseki" | "neo4j",   # 생략 시 env GRAPHSTORE_BACKEND, 그것도 없으면 "fuseki"
        "base_url": ..., "dataset": ..., "username": ..., "password": ...,  # fuseki 선택
        "uri": ..., "database": ...,                                        # neo4j 선택
    }
    설정을 생략하면 백엔드가 env(FUSEKI_URL / NEO4J_URI 등)에서 읽는다.
    ⭐ 스왑은 여기 한 곳뿐 — documents 는 create_store 만 호출하고 백엔드를 모른다.
    미지 백엔드는 명시적 에러(조용한 fallback 금지).
    """
    config = dict(config or {})
    backend = (config.pop("backend", None) or os.getenv("GRAPHSTORE_BACKEND") or "fuseki").lower()
    if backend == "fuseki":
        return FusekiBackend(
            base_url=config.get("base_url"),
            dataset=config.get("dataset"),
            username=config.get("username"),
            password=config.get("password"),
        )
    if backend == "neo4j":
        from xgen_graphstore.neo4j_backend import Neo4jBackend  # 지연 import(optional dep)

        return Neo4jBackend(
            uri=config.get("uri") or config.get("base_url"),
            username=config.get("username"),
            password=config.get("password"),
            database=config.get("database"),
        )
    raise UnknownBackendError(
        f"알 수 없는 backend: {backend!r} (지원: 'fuseki', 'neo4j'). "
        f"신규 백엔드는 이 팩토리에 분기 1개만 추가 — DEBTS.md 참조."
    )
