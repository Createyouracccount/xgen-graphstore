"""라우터 R1 — 레지스트리 + 능력계약 + 명확차단 (ADR-003). 서버 불필요."""

import pytest

from xgen_graphstore import (
    Capability,
    CapabilityError,
    UnknownBackendError,
    Workload,
    available_backends,
    create_store,
    preflight_report,
    probe_workload,
    register_backend,
    require_capability,
    require_workload,
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


# ── 워크로드 프리플라이트 — 무증상 실패 차단 (§15.4 실측 근거) ──

def test_preflight_detects_search_unavailable_on_lpg():
    """LPG 백엔드는 검색 워크로드 미보유를 **부팅 시점에** 드러낸다(조용한 빈 결과 방지)."""
    neo = create_store({"backend": "neo4j"})
    r = probe_workload(neo, Workload.GRAPH_SEARCH)
    assert r["ok"] is False
    assert len(r["missing"]) == 7, "검색 7메서드 전부 미보유여야 함(실측)"
    assert r["backend"] == "neo4j"


def test_preflight_fuseki_search_ok():
    """Fuseki 는 검색 7/7 보유 — 현재 유일하게 검색되는 백엔드."""
    fus = create_store({"backend": "fuseki"})
    r = probe_workload(fus, Workload.GRAPH_SEARCH)
    assert r["ok"] is True
    assert r["missing"] == []


def test_require_workload_blocks_silent_degrade():
    """검색 요구 시 LPG 는 CapabilityError 로 **차단**된다 — 무증상 degrade 대신 명시적 실패."""
    neo = create_store({"backend": "neo4j"})
    with pytest.raises(CapabilityError) as ei:
        require_workload(neo, Workload.GRAPH_SEARCH)
    assert "graph_search" in str(ei.value)
    # Fuseki 는 통과(예외 없음)
    require_workload(create_store({"backend": "fuseki"}), Workload.GRAPH_SEARCH)


def test_require_workload_algo_split_across_backends():
    """알고리즘은 Neo4j만, 검색은 Fuseki만 — 어느 백엔드도 전부를 하지 못한다(실측 현실)."""
    neo = create_store({"backend": "neo4j"})
    fus = create_store({"backend": "fuseki"})
    require_workload(neo, Workload.GRAPH_ALGO)          # Neo4j=GDS 보유 → 통과
    with pytest.raises(CapabilityError):
        require_workload(fus, Workload.GRAPH_ALGO)      # Fuseki=알고리즘 불가
    # 코어 CRUD 는 둘 다 통과(B4/B5 증명 범위)
    require_workload(neo, Workload.CORE_CRUD)
    require_workload(fus, Workload.CORE_CRUD)


def test_preflight_report_names_missing_methods():
    """리포트가 미보유 메서드를 실제로 열거해야 한다(진단 가치)."""
    txt = preflight_report(create_store({"backend": "neo4j"}), [Workload.GRAPH_SEARCH])
    assert "UNAVAILABLE" in txt
    assert "seed_connectivity_relations" in txt
    assert "조용히 빈 결과" in txt
