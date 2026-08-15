"""xgen-graphstore — 백엔드 중립 온톨로지 그래프 저장소.

공개 표면:
- OntologyStore: 백엔드 중립 의미 인터페이스(Protocol)
- FusekiBackend: Apache Jena Fuseki 구현 (transport 상속 + 의미 메서드)
- create_store(config): 백엔드 선택 팩토리
- GraphStoreError / UnknownBackendError: 예외
- set_call_timer: 선택적 호출 계측 훅 주입

Provenance: xgen-documents service/ontology/{fuseki_client,fuseki_queries,
fuseki_backend,ontology_store}.py 에서 이관. 출처 커밋 8a81e23.
"""

from __future__ import annotations

from xgen_graphstore._calltimer import set_call_timer
from xgen_graphstore.backend import FusekiBackend
from xgen_graphstore.errors import GraphStoreError, UnknownBackendError
from xgen_graphstore.factory import create_store
from xgen_graphstore.store import OntologyStore
from xgen_graphstore.transport import FusekiClient

__version__ = "0.1.0"

__all__ = [
    "OntologyStore",
    "FusekiBackend",
    "FusekiClient",
    "create_store",
    "GraphStoreError",
    "UnknownBackendError",
    "set_call_timer",
    "__version__",
]
