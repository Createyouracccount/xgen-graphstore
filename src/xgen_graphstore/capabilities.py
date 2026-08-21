"""백엔드 능력 계약 — 라우터가 "이 DB가 이 연산을 하는가"를 아는 근거 (ADR-003 R1).

각 백엔드는 `CAPABILITIES`(frozenset[Capability])를 선언한다. 미지원 연산은 조용한
오동작 대신 CapabilityError 로 명확 차단한다(프로젝트 '회색지대 기본값 금지' 원칙).
R2(멀티백엔드 라우팅)는 이 선언을 라우팅 표로 쓴다.
"""

from __future__ import annotations

from enum import Enum


class Capability(str, Enum):
    CORE_TRIPLE_RW = "core_triple_rw"    # insert/delete/exists/count/neighbors/node props — 실백엔드 필수
    FULLTEXT_SEARCH = "fulltext_search"  # text:query / fulltext index (DEBTS §A)
    NAMED_GRAPH = "named_graph"          # named-graph staging/commit/clear (DEBTS §B)
    OWL_SCHEMA = "owl_schema"            # OWL/RDFS-as-data 스키마·집계 (DEBTS §D)
    TTL_UPLOAD = "ttl_upload"            # Turtle 업로드 (DEBTS §E)
    RAW_QUERY = "raw_query"              # 백엔드 고유 자유질의(SPARQL 등)
    GRAPH_ALGORITHMS = "graph_algorithms"  # 반복형 그래프알고리즘(커뮤니티탐지/PageRank) — 엔진 in-DB 실행 (§13 실측)


# 특수 능력을 요구하는 메서드만 등록. 미등록 메서드는 CORE(모든 실백엔드 필수)로 간주.
# → 미구현 시: 매핑된 메서드=CapabilityError(근본 능력 공백), 그 외=NotImplementedError(구현 가능·미완).
METHOD_CAPABILITY: dict[str, Capability] = {
    "seed_relations_by_fulltext_forward": Capability.FULLTEXT_SEARCH,
    "seed_relations_by_fulltext_reverse": Capability.FULLTEXT_SEARCH,
    "seed_connectivity_relations": Capability.FULLTEXT_SEARCH,
    "seed_relations_broad": Capability.FULLTEXT_SEARCH,
    "seed_classes_by_fulltext": Capability.FULLTEXT_SEARCH,
    "commit_staged_graph": Capability.NAMED_GRAPH,
    "get_ingest_commit_marker": Capability.NAMED_GRAPH,
    "clear_graph": Capability.NAMED_GRAPH,
    "get_tbox_schema": Capability.OWL_SCHEMA,
    "get_graph_data_for_visualization": Capability.OWL_SCHEMA,
    "clean_subclassof_noise": Capability.OWL_SCHEMA,
    "materialize_property_inheritance": Capability.OWL_SCHEMA,
    "count_classes": Capability.OWL_SCHEMA,
    "count_properties": Capability.OWL_SCHEMA,
    "upload_ttl": Capability.TTL_UPLOAD,
    "sparql_query": Capability.RAW_QUERY,
    "sparql_update": Capability.RAW_QUERY,
    "community_detect": Capability.GRAPH_ALGORITHMS,
    "pagerank": Capability.GRAPH_ALGORITHMS,
}


def declared_capabilities(store) -> frozenset:
    return getattr(store, "CAPABILITIES", frozenset())


def supports(store, cap: Capability) -> bool:
    """백엔드가 능력을 선언했는가. 미선언 백엔드는 항상 False(단정 불가는 미지원 취급)."""
    return cap in declared_capabilities(store)


def require_capability(store, cap: Capability) -> None:
    """미지원이면 CapabilityError. 능력 미선언 백엔드는 검사 생략(단정 불가 → 백엔드가 스스로 실패)."""
    caps = declared_capabilities(store)
    if caps and cap not in caps:
        from xgen_graphstore.errors import CapabilityError

        name = getattr(store, "BACKEND_NAME", type(store).__name__)
        raise CapabilityError(
            f"backend '{name}' 는 '{cap.value}' 능력 미지원 — DEBTS.md 참조. "
            f"(무증상 오동작 대신 명확 차단)"
        )
