"""라우터 R1 — 레지스트리 + 능력계약 + 명확차단 (ADR-003). 서버 불필요."""

import pytest

from xgen_graphstore import (
    Capability,
    CapabilityError,
    UnknownBackendError,
    available_backends,
    create_store,
    register_backend,
    require_capability,
    supports,
)


def test_builtin_backends_registered():
    assert "fuseki" in available_backends()
    assert "neo4j" in available_backends()


def test_registry_pluggable_any_backend():
    """어떤 DB든 register_backend 로 꽂으면 create_store 로 선택 (코어 수정 없이)."""
    class DummyStore:
        BACKEND_NAME = "dummy"
        CAPABILITIES = frozenset({Capability.CORE_TRIPLE_RW})

    register_backend("dummy", lambda cfg: DummyStore())
    assert "dummy" in available_backends()
    assert type(create_store({"backend": "dummy"})).__name__ == "DummyStore"


def test_unknown_backend_raises_not_silent():
    with pytest.raises(UnknownBackendError):
        create_store({"backend": "cassandra"})


def test_default_is_fuseki():
    assert type(create_store()).__name__ == "FusekiBackend"


def test_capability_introspection_fuseki_all_except_graph_algos():
    """Fuseki=RDF/SPARQL 원본이라 대부분 보유하되, 반복형 그래프알고리즘은 in-DB 불가(§11.3)."""
    fus = create_store({"backend": "fuseki"})  # 생성만(연결 없음)
    for cap in Capability:
        if cap is Capability.GRAPH_ALGORITHMS:
            assert supports(fus, cap) is False, "Fuseki는 Louvain/PageRank in-DB 불가"
        else:
            assert supports(fus, cap) is True, cap


def test_capability_introspection_neo4j_core_and_graph_algos():
    """Neo4j=코어 트리플 + GDS 그래프알고리즘 보유(§13 채택), fulltext/owl 는 PoC 미보유."""
    neo = create_store({"backend": "neo4j"})   # 드라이버 생성만(연결 없음)
    assert supports(neo, Capability.CORE_TRIPLE_RW) is True
    assert supports(neo, Capability.GRAPH_ALGORITHMS) is True
    assert supports(neo, Capability.FULLTEXT_SEARCH) is False
    assert supports(neo, Capability.OWL_SCHEMA) is False


def test_graph_algorithms_only_neo4j_among_backends():
    """GRAPH_ALGORITHMS 는 Neo4j만 선언 — 라우터가 알고리즘을 Neo4j로 보낼 근거(§13)."""
    neo = create_store({"backend": "neo4j"})
    fus = create_store({"backend": "fuseki"})
    arc = create_store({"backend": "arcade"})
    assert supports(neo, Capability.GRAPH_ALGORITHMS) is True
    assert supports(fus, Capability.GRAPH_ALGORITHMS) is False
    assert supports(arc, Capability.GRAPH_ALGORITHMS) is False


def test_arcade_community_detect_is_capability_error_not_silent():
    """arcade 에서 community_detect = CapabilityError(회색지대 금지), 조용한 실패 아님."""
    arc = create_store({"backend": "arcade"})
    with pytest.raises(CapabilityError):
        arc.community_detect


def test_require_capability_clear_block():
    neo = create_store({"backend": "neo4j"})
    with pytest.raises(CapabilityError):
        require_capability(neo, Capability.FULLTEXT_SEARCH)
    require_capability(neo, Capability.CORE_TRIPLE_RW)  # 지원 → 통과(예외 없음)


def test_neo4j_unsupported_method_is_capability_error_not_silent():
    """미지원 fulltext 메서드 접근 = CapabilityError(라우팅 신호), 조용한 실패 아님."""
    neo = create_store({"backend": "neo4j"})
    with pytest.raises(CapabilityError):
        neo.seed_classes_by_fulltext  # __getattr__ → 능력 매핑 → CapabilityError


def test_neo4j_unbuilt_but_possible_method_is_notimplemented():
    """구현 가능하나 PoC 미완(rename_*) = NotImplementedError(능력 공백과 구분)."""
    neo = create_store({"backend": "neo4j"})
    with pytest.raises(NotImplementedError):
        neo.rename_move_subject
