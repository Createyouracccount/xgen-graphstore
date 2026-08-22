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
    """Neo4j=코어 + GDS 알고리즘(§13) + fulltext(DEBTS §A 이식완료). owl-as-data 는 여전히 미보유."""
    neo = create_store({"backend": "neo4j"})   # 드라이버 생성만(연결 없음)
    assert supports(neo, Capability.CORE_TRIPLE_RW) is True
    assert supports(neo, Capability.GRAPH_ALGORITHMS) is True
    assert supports(neo, Capability.FULLTEXT_SEARCH) is True
    assert supports(neo, Capability.OWL_SCHEMA) is False


def test_graph_algorithms_only_neo4j_among_backends():
    """GRAPH_ALGORITHMS 는 Neo4j만 선언 — 라우터가 알고리즘을 Neo4j로 보낼 근거(§13)."""
    neo = create_store({"backend": "neo4j"})
    fus = create_store({"backend": "fuseki"})
    arc = create_store({"backend": "arcade"})
    assert supports(neo, Capability.GRAPH_ALGORITHMS) is True
    assert supports(fus, Capability.GRAPH_ALGORITHMS) is False
    assert supports(arc, Capability.GRAPH_ALGORITHMS) is False
    # fulltext 는 3백엔드 모두 이식 완료(DEBTS §A) — 단 매칭 특성은 엔진마다 다르다(실측 공시).
    assert supports(arc, Capability.FULLTEXT_SEARCH) is True


def test_arcade_community_detect_is_capability_error_not_silent():
    """arcade 에서 community_detect = CapabilityError(회색지대 금지), 조용한 실패 아님."""
    arc = create_store({"backend": "arcade"})
    with pytest.raises(CapabilityError):
        arc.community_detect


def test_require_capability_clear_block():
    neo = create_store({"backend": "neo4j"})
    with pytest.raises(CapabilityError):
        require_capability(neo, Capability.OWL_SCHEMA)   # 여전히 미보유
    require_capability(neo, Capability.CORE_TRIPLE_RW)   # 지원 → 통과(예외 없음)
    require_capability(neo, Capability.FULLTEXT_SEARCH)  # 이식 완료 → 통과


def test_unsupported_method_is_capability_error_not_silent():
    """미지원 능력 메서드 접근 = CapabilityError(라우팅 신호), 조용한 실패 아님."""
    for backend in ("neo4j", "arcade"):
        st = create_store({"backend": backend})
        with pytest.raises(CapabilityError):
            st.get_tbox_schema        # OWL_SCHEMA 는 두 LPG 백엔드 모두 미보유


def test_neo4j_unbuilt_but_possible_method_is_notimplemented():
    """구현 가능하나 PoC 미완(rename_*) = NotImplementedError(능력 공백과 구분)."""
    neo = create_store({"backend": "neo4j"})
    with pytest.raises(NotImplementedError):
        neo.rename_move_subject


# ── 워크로드 프리플라이트 — 무증상 실패 차단 (§15.4 실측 근거) ──

class _PartialBackend:
    """검색을 일부만 갖춘 가상 백엔드 — 새 DB 를 꽂았을 때의 상태를 대표한다.

    실제 백엔드들이 모두 이식을 마쳐도 이 계약(부분 보유 감지)은 계속 검증돼야 하므로
    구현체가 아니라 더미로 고정한다.
    """
    BACKEND_NAME = "partial"
    CAPABILITIES = frozenset({Capability.CORE_TRIPLE_RW})

    def seed_chunk_relations(self, *a, **k):
        raise NotImplementedError

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        raise CapabilityError(f"partial 백엔드는 '{name}' 미지원")


def test_preflight_detects_search_unavailable_on_lpg():
    """검색 워크로드 **부분 보유**를 부팅 시점에 드러낸다(조용한 빈 결과 방지)."""
    register_backend("partial", lambda cfg: _PartialBackend())
    st = create_store({"backend": "partial"})
    r = probe_workload(st, Workload.GRAPH_SEARCH)
    assert r["ok"] is False, "검색 미보유 백엔드는 워크로드 불가로 드러나야 함"
    assert "seed_chunk_relations" in r["present"]
    assert len(r["missing"]) == 6
    assert r["backend"] == "partial"


def test_preflight_all_real_backends_search_complete():
    """3백엔드 모두 검색 7/7 이식 완료 — '어떤 DB 를 꽂아도 동일 동작' 의 계약 상태."""
    for backend in ("fuseki", "neo4j", "arcade"):
        r = probe_workload(create_store({"backend": backend}), Workload.GRAPH_SEARCH)
        assert r["ok"] is True, f"{backend} 검색 미보유: {r['missing']}"


def test_preflight_neo4j_search_complete_after_port():
    """Neo4j 는 fulltext 5종 이식으로 검색 워크로드 7/7 을 갖췄다(DEBTS §A 완료 신호)."""
    neo = create_store({"backend": "neo4j"})
    r = probe_workload(neo, Workload.GRAPH_SEARCH)
    assert r["ok"] is True, f"검색 미보유 남음: {r['missing']}"
    assert len(r["present"]) == 7


def test_preflight_fuseki_search_ok():
    """Fuseki 는 검색 7/7 보유(원본 기준 백엔드)."""
    fus = create_store({"backend": "fuseki"})
    r = probe_workload(fus, Workload.GRAPH_SEARCH)
    assert r["ok"] is True
    assert r["missing"] == []


def test_require_workload_blocks_silent_degrade():
    """검색 미보유 백엔드는 CapabilityError 로 **차단** — 무증상 degrade 대신 명시적 실패."""
    register_backend("partial", lambda cfg: _PartialBackend())
    with pytest.raises(CapabilityError) as ei:
        require_workload(create_store({"backend": "partial"}), Workload.GRAPH_SEARCH)
    assert "graph_search" in str(ei.value)
    # 이식을 마친 백엔드는 통과(예외 없음)
    for backend in ("fuseki", "neo4j", "arcade"):
        require_workload(create_store({"backend": backend}), Workload.GRAPH_SEARCH)


def test_require_workload_algo_split_across_backends():
    """알고리즘은 Neo4j만 보유 — 워크로드별로 가능한 백엔드가 갈린다(라우팅 근거)."""
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
    register_backend("partial", lambda cfg: _PartialBackend())
    txt = preflight_report(create_store({"backend": "partial"}), [Workload.GRAPH_SEARCH])
    assert "UNAVAILABLE" in txt
    assert "seed_connectivity_relations" in txt
    assert "조용히 빈 결과" in txt
