"""graphstore 라우터 — 백엔드 레지스트리 + 선택 (ADR-003 R1).

"어떤 DB든 register_backend 로 꽂고, 이름으로 선택." 현재는 **단일 활성 백엔드** 라우팅.
멀티백엔드 능력라우팅(R2)·듀얼라이트(R3)는 이 레지스트리·능력계약 위에 얹는다.
미지 백엔드는 조용한 fallback 없이 UnknownBackendError.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from xgen_graphstore.errors import UnknownBackendError

_REGISTRY: Dict[str, Callable[[Dict[str, Any]], Any]] = {}


def register_backend(name: str, factory: Callable[[Dict[str, Any]], Any]) -> None:
    """백엔드 등록. 어떤 DB든 여기 등록하면 create_store 로 선택 가능(코어 수정 불필요)."""
    _REGISTRY[name.lower()] = factory


def available_backends() -> List[str]:
    return sorted(_REGISTRY)


def create_store(config: Optional[Dict[str, Any]] = None):
    """config 로 백엔드를 선택해 반환(라우터의 선택 함수).

    backend 는 config["backend"] → env GRAPHSTORE_BACKEND → "fuseki" 순. ⭐ 스왑점은 여기 한 곳.
    """
    config = dict(config or {})
    name = (config.pop("backend", None) or os.getenv("GRAPHSTORE_BACKEND") or "fuseki").lower()
    factory = _REGISTRY.get(name)
    if factory is None:
        raise UnknownBackendError(
            f"알 수 없는 backend: {name!r} (등록됨: {available_backends()}). "
            f"register_backend({name!r}, ...) 로 등록하거나 DEBTS.md 참조."
        )
    return factory(config)


# ── 내장 백엔드 등록 (구현 모듈은 지연 import — neo4j 는 optional dep) ──

def _fuseki_factory(cfg: Dict[str, Any]):
    from xgen_graphstore.backend import FusekiBackend

    return FusekiBackend(
        base_url=cfg.get("base_url"),
        dataset=cfg.get("dataset"),
        username=cfg.get("username"),
        password=cfg.get("password"),
    )


def _neo4j_factory(cfg: Dict[str, Any]):
    from xgen_graphstore.neo4j_backend import Neo4jBackend

    return Neo4jBackend(
        uri=cfg.get("uri") or cfg.get("base_url"),
        username=cfg.get("username"),
        password=cfg.get("password"),
        database=cfg.get("database"),
    )


register_backend("fuseki", _fuseki_factory)
register_backend("neo4j", _neo4j_factory)
