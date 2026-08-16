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


def test_capability_introspection_fuseki_all():
    fus = create_store({"backend": "fuseki"})  # 생성만(연결 없음)
    for cap in Capability:
        assert supports(fus, cap) is True, cap


def test_capability_introspection_neo4j_core_only():
    neo = create_store({"backend": "neo4j"})   # 드라이버 생성만(연결 없음)
    assert supports(neo, Capability.CORE_TRIPLE_RW) is True
    assert supports(neo, Capability.FULLTEXT_SEARCH) is False
    assert supports(neo, Capability.OWL_SCHEMA) is False


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
