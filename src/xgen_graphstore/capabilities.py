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


def implemented_methods(store_or_cls) -> list:
    """이 백엔드가 **실제로** 구현한 공개 메서드 이름. 진단 메시지용.

    하드코딩하면 낡는다 — 실측(0830): Neo4jBackend 는 26개를 구현하는데
    `__getattr__` 의 NotImplementedError 메시지는 7개만 광고하고 있었다(0823 검색 이식·
    빌드 경로 이식 이전 목록). 그 메시지를 본 개발자는 fulltext·upload_ttl·
    clean_subclassof_noise 가 없다고 오인한다.

    ⚠️ `dir()`/`getattr()` 를 쓰면 백엔드의 `__getattr__` 가 다시 불려 재귀한다.
    MRO 의 `vars()` 만 훑는다.
    """
    cls = store_or_cls if isinstance(store_or_cls, type) else type(store_or_cls)
    out: set = set()
    for base in cls.__mro__:
        if base is object:
            continue
        for n, v in vars(base).items():
            if not n.startswith("_") and callable(v):
                out.add(n)
    return sorted(out)


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


# ─────────────────────────────────────────────────────────────────────────────
# 워크로드 프리플라이트 — 무증상 실패 차단 (§15.4 실측 근거)
#
# 배경: 검색 호출부(documents multi_turn_rag)가 `except Exception` 으로 전부 흡수한다.
# 그래서 능력 미보유 백엔드로 스왑하면 **예외도 로그도 없이** 빈 그래프 근거로 degrade하고,
# 답변은 정상처럼 보이지만 실제론 벡터검색만 돈다(실측: neo4j/arcade 검색 0/7).
#
# 런타임에 삼켜지는 것은 graphstore 가 막을 수 없다 → **부팅 시점에 명시적으로 드러낸다.**
# 프로젝트 원칙 "회색지대 기본값 금지: 판단 불가는 명시적 통과 또는 명시적 차단".
# ─────────────────────────────────────────────────────────────────────────────

class Workload(str, Enum):
    """백엔드가 실제로 수행해야 하는 작업 단위(메서드 묶음)."""
    CORE_CRUD = "core_crud"          # 트리플 읽기/쓰기/병합 (B4·B5 증명 범위)
    GRAPH_SEARCH = "graph_search"    # multi_turn_rag.query 검색 경로 전체
    GRAPH_ALGO = "graph_algo"        # 커뮤니티탐지·PageRank (GDS 등)
    ONTOLOGY_BUILD = "ontology_build"  # 문서→그래프 적재 (kg_builder.build_and_upload)
    GRAPH_BROWSE = "graph_browse"    # 프론트 그래프 탐색/시각화


# 각 워크로드가 실제로 호출하는 메서드 (documents 코드 실사 근거).
WORKLOAD_METHODS: dict[Workload, tuple[str, ...]] = {
    Workload.CORE_CRUD: (
        "insert_data", "delete_data", "triple_exists", "count_node_triples",
        "merge_move_subject", "merge_move_object",
    ),
    # multi_turn_rag.py 가 self.fuseki.* 로 부르는 전부(:209/486/532/533/565/573/587).
    Workload.GRAPH_SEARCH: (
        "seed_connectivity_relations",          # 매 질의 1차 시드
        "seed_classes_by_fulltext",             # 매 질의 클래스 전수
        "seed_relations_broad",                 # 연결성 빈 결과시 유일 recall
        "seed_relations_by_fulltext_forward",   # 정방향 정밀관계
        "seed_relations_by_fulltext_reverse",   # 역방향 정밀관계
        "predicate_labels",                     # 관계형 게이트
        "seed_chunk_relations",                 # 1홉 확장(HippoRAG) — 정밀 SVO 슬롯
        "seed_chunk_cooccurrence",              # 동시출현 약관계 슬롯(0824) — SVO 와 LIMIT 분리
    ),
    Workload.GRAPH_ALGO: ("community_detect", "pagerank"),
    # kg_builder.build_and_upload 실제 호출 순서(코드 실사):
    #   ensure_dataset → clear_graph → upload_ttl → clean_subclassof_noise
    #   → materialize_property_inheritance, 이후 파이프라인이 get_triple_count 로 검증하고
    #   중복 병합(merge_*)·rename(rename_*)·ingest 커밋(commit_staged_graph)을 수행한다.
    # ⚠️ 이 중 하나라도 없으면 빌드는 **그 지점에서 죽는다**(검색과 달리 조용하지 않다).
    Workload.ONTOLOGY_BUILD: (
        "ensure_dataset", "clear_graph", "upload_ttl",
        "clean_subclassof_noise", "materialize_property_inheritance",
        "get_triple_count", "get_ingest_commit_marker", "commit_staged_graph",
        "merge_normalized_instances_labels", "merge_move_subject", "merge_move_object",
        "merge_journal_insert",                 # 병합 저널(0824) — 물리 병합이 비가역이라 필수
        "same_label_nodes",
        "rename_move_subject", "rename_move_object", "rename_drop_old_label",
    ),
    # 프론트 그래프 탐색(graph_rag_operations.py) — 질의 검색이 아니라 브라우징 경로.
    Workload.GRAPH_BROWSE: (
        "get_graph_data_for_visualization", "node_properties", "property_values",
        "neighbors", "community_edges", "community_labels", "tag_communities",
    ),
}


def probe_workload(store, workload: Workload) -> dict:
    """백엔드가 워크로드를 수행 가능한지 **호출 없이** 진단한다.

    반환: {"backend", "workload", "ok", "missing": [(method, reason), ...], "present": [...]}
    `ok=False` 면 그 워크로드는 이 백엔드에서 성립하지 않는다(조용히 빈 결과가 될 자리).
    """
    name = getattr(store, "BACKEND_NAME", type(store).__name__)
    missing, present = [], []
    for m in WORKLOAD_METHODS[workload]:
        try:
            getattr(store, m)
            present.append(m)
        except Exception as e:                      # CapabilityError / NotImplementedError 등
            missing.append((m, type(e).__name__))
    return {
        "backend": name,
        "workload": workload.value,
        "ok": not missing,
        "missing": missing,
        "present": present,
    }


def require_workload(store, workload: Workload) -> None:
    """워크로드 수행 불가면 **부팅 시점에** CapabilityError로 차단(무증상 degrade 방지).

    조용한 빈 결과를 원치 않는 경로(예: 그래프 검색이 제품 기능인 배포)에서 부팅 훅으로 호출한다.
    """
    r = probe_workload(store, workload)
    if r["ok"]:
        return
    from xgen_graphstore.errors import CapabilityError

    detail = ", ".join(f"{m}({why})" for m, why in r["missing"])
    raise CapabilityError(
        f"backend '{r['backend']}' 는 워크로드 '{workload.value}' 수행 불가 — "
        f"미보유 {len(r['missing'])}/{len(WORKLOAD_METHODS[workload])}: {detail}. "
        f"이 상태로 기동하면 호출부의 except 흡수로 **무증상 빈 결과**가 된다(§15.4). "
        f"백엔드를 바꾸거나 해당 메서드를 이식할 것."
    )


def preflight_report(store, workloads=None) -> str:
    """부팅 로그용 사람이 읽는 진단 리포트. 미보유를 **명시적으로 드러내는 것**이 목적."""
    workloads = workloads or list(Workload)
    lines = []
    for w in workloads:
        r = probe_workload(store, w)
        mark = "OK" if r["ok"] else "UNAVAILABLE"
        lines.append(
            f"[graphstore preflight] backend={r['backend']} workload={w.value}: {mark} "
            f"({len(r['present'])}/{len(WORKLOAD_METHODS[w])} 메서드 보유)"
        )
        if not r["ok"]:
            lines.append(
                "    미보유: " + ", ".join(f"{m}({why})" for m, why in r["missing"])
            )
            lines.append(
                "    ⚠️ 이 워크로드는 조용히 빈 결과가 된다(호출부 except 흡수) — 사용 전 이식 필요."
            )
    return "\n".join(lines)
