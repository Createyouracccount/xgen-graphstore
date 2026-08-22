"""xgen-graphstore — 백엔드 중립 온톨로지 그래프 저장소 **라우터**.

공개 표면:
- OntologyStore: 백엔드 중립 의미 인터페이스(Protocol)
- FusekiBackend: Apache Jena Fuseki(RDF/SPARQL) 구현. Neo4j(LPG) 구현은 create_store 로 선택(optional dep).
- create_store(config): 라우터의 백엔드 선택 함수 (backend: fuseki|neo4j|등록된 이름)
- register_backend(name, factory) / available_backends(): 어떤 DB든 등록·조회 (ADR-003 R1)
- Capability / supports / require_capability: 백엔드 능력 계약 (무증상 오동작 대신 명확 차단)
- GraphStoreError / UnknownBackendError / CapabilityError: 예외
- set_call_timer: 선택적 호출 계측 훅 주입

Provenance: xgen-documents service/ontology/{fuseki_client,fuseki_queries,
fuseki_backend,ontology_store}.py 에서 이관. 출처 커밋 8a81e23.
"""

from __future__ import annotations

from xgen_graphstore._calltimer import set_call_timer
from xgen_graphstore.backend import FusekiBackend
from xgen_graphstore.capabilities import (
    Capability,
    Workload,
    preflight_report,
    probe_workload,
    require_capability,
    require_workload,
    supports,
)
from xgen_graphstore.errors import (
    CapabilityError,
    GraphStoreError,
    UnknownBackendError,
)
from xgen_graphstore.router import available_backends, create_store, register_backend
from xgen_graphstore.store import OntologyStore
from xgen_graphstore.transport import FusekiClient

__version__ = "0.1.0"

__all__ = [
    "OntologyStore",
    "FusekiBackend",
    "FusekiClient",
    "create_store",
    "register_backend",
    "available_backends",
    "Capability",
    "supports",
    "require_capability",
    "Workload",
    "probe_workload",
    "require_workload",
    "preflight_report",
    "GraphStoreError",
    "UnknownBackendError",
    "CapabilityError",
    "set_call_timer",
    "__version__",
]
