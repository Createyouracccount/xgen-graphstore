"""봉인 골든 + 목-transport 파싱 등가 — 문서 리포 이력 무의존(자립).

documents 쪽 골든은 git-앵커드(이관 전 소스 재구성)라 documents 이력에 의존한다.
패키지는 그 이력이 없으므로, 이관 시점에 바이트 동일이 증명된 쿼리 문자열을 REF 픽스처로
**봉인**해 자립 검증한다. (documents 골든이 원본↔빌더 동등을 이미 증명했고, 여기서는
빌더↔봉인REF 동등 = 패키지가 그 빌더를 무변경 보유함을 지킨다.)
"""

import asyncio

import pytest

from xgen_graphstore import queries as q
from xgen_graphstore.backend import FusekiBackend

GRAPH = "https://w3id.org/xgen/collection/롯데-2026_a"
NODE = "https://w3id.org/xgen-instance#삼성전자(주)"
PROP = "https://w3id.org/xgen-domain#설립연도"
TERMS = "삼성 인수"
PIN = '"인수", "합병"'
VALUES = '"c1" "c2"'
LIMIT = 100


# ── 봉인 골든: 빌더 출력이 봉인 REF 와 바이트 동일 ──

def test_sealed_community_edges():
    assert q.community_edges_query(GRAPH) == (
        f"SELECT ?s ?o WHERE {{ GRAPH <{GRAPH}> {{ "
        "?s <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/2002/07/owl#NamedIndividual> . "
        "?s ?p ?o . ?o <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/2002/07/owl#NamedIndividual> . "
        "FILTER(?p != <http://www.w3.org/1999/02/22-rdf-syntax-ns#type>) } }")


def test_sealed_community_tag_delete():
    assert q.community_tag_delete_update(GRAPH) == (
        f"WITH <{GRAPH}> DELETE {{ ?s <https://w3id.org/xgen-domain#community> ?c }} "
        "WHERE { ?s <https://w3id.org/xgen-domain#community> ?c }")


def test_sealed_merge_moves_two_sided_and_scope():
    uri = "https://w3id.org/xgen-instance#한국마사회를"
    can = "https://w3id.org/xgen-instance#한국마사회"
    assert q.merge_move_subject_update(GRAPH, uri, can) == (
        f"WITH <{GRAPH}> DELETE {{ <{uri}> ?p ?o }} INSERT {{ <{can}> ?p ?o }} WHERE {{ <{uri}> ?p ?o }}")
    assert q.merge_move_object_update(GRAPH, uri, can) == (
        f"WITH <{GRAPH}> DELETE {{ ?s ?p <{uri}> }} INSERT {{ ?s ?p <{can}> }} WHERE {{ ?s ?p <{uri}> }}")


def test_sealed_rename_three_step():
    rdfs = "http://www.w3.org/2000/01/rdf-schema#"
    ol, nl = "감사", "감사역"
    assert q.rename_move_subject_update(GRAPH, rdfs, ol, nl) == (
        f'PREFIX rdfs: <{rdfs}> WITH <{GRAPH}> DELETE {{ ?old ?p ?o }} INSERT {{ ?new ?p ?o }} '
        f'WHERE {{ ?old rdfs:label "{ol}"@ko . ?new rdfs:label "{nl}"@ko . FILTER(?old != ?new) . ?old ?p ?o }}')
    assert q.rename_drop_old_label_update(GRAPH, rdfs, ol, nl) == (
        f'PREFIX rdfs: <{rdfs}> WITH <{GRAPH}> DELETE {{ ?new rdfs:label "{ol}"@ko }} '
        f'WHERE {{ ?new rdfs:label "{nl}"@ko . ?new rdfs:label "{ol}"@ko }}')


def test_sealed_text_query_thresholds():
    # text:query Lucene 임계(15/80/60/30)가 봉인됨 — 3층 LPG 재보정 대상(DEBTS)
    assert '"삼성 인수" 15)' in q.seed_relations_by_fulltext_forward_query(GRAPH, TERMS, PIN)
    assert '"삼성 인수" 80)' in q.seed_connectivity_relations_query(GRAPH, TERMS, LIMIT)
    assert '"삼성 인수" 60)' in q.seed_relations_broad_query(GRAPH, TERMS, LIMIT)
    assert '"삼성 인수" 30)' in q.seed_classes_by_fulltext_query(GRAPH, TERMS)


# ── 0824 전방이식분 봉인 ──
#
# 추출본이 정본보다 낡아 실측 채택 동작이 조용히 되돌아간 사고(DEBTS §G) 뒤에 봉인한다.
# 이 문자열들은 xgen-documents `ontology-search` 정본과 바이트 동일임을 확인하고 고정했다.

_NS = "https://w3id.org/xgen-domain#"
_PRED = ("FILTER(?p != rdf:type && ?p != rdfs:label && ?p != :sourceChunk "
         "&& ?p != :sourceDocument && ?p != :scsContextSummary) ")
_CHUNK_BASE = (
    f"PREFIX : <{_NS}> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
    "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> "
    f"SELECT DISTINCT ?sLabel ?pLabel ?oLabel WHERE {{ GRAPH <{GRAPH}> {{ "
    f"VALUES ?c {{ {VALUES} }} ?s :sourceChunk ?c . ?s rdfs:label ?sLabel . ?s ?p ?o . " + _PRED)


def test_sealed_chunk_seed_slot_split():
    """정밀 SVO 와 동시출현 약관계는 **슬롯이 나뉘어야** 한다.

    단일 정렬 LIMIT 는 SVO 가 항상 선점해 co-occ 가 0 이 된다(mixed20k 실측).
    두 슬롯이 같은 base 를 공유하되 술어 필터만 반대인 것이 계약이다.
    """
    assert q.seed_chunk_relations_query(GRAPH, VALUES, 60) == (
        _CHUNK_BASE + "FILTER(?p != :coOccursWith) "
        "?o rdfs:label ?oLabel . OPTIONAL { ?p rdfs:label ?pLabel } } } LIMIT 60")
    # cq 는 술어가 단일이라 ?pLabel 을 조회하지 않는다(ko/en 2행 중복으로 슬롯 낭비 방지).
    assert q.seed_chunk_cooccurrence_query(GRAPH, VALUES, 12) == (
        _CHUNK_BASE.replace("?sLabel ?pLabel ?oLabel", "?sLabel ?oLabel")
        + "FILTER(?p = :coOccursWith) ?o rdfs:label ?oLabel . } } LIMIT 12")


def test_sealed_connectivity_excludes_cooccurrence():
    """정밀 시드(connectivity)는 동시출현을 제외하고, recall 폴백(broad)은 포함한다.

    broad 에까지 걸면 SVO 가 없는 희소 구간의 유일한 recall 을 잃는다.
    """
    conn = q.seed_connectivity_relations_query(GRAPH, TERMS, LIMIT)
    broad = q.seed_relations_broad_query(GRAPH, TERMS, LIMIT)
    assert "FILTER(?s != ?o) " + _PRED + "FILTER(?p != :coOccursWith) " in conn
    assert ":coOccursWith" not in broad


def test_sealed_class_seed_closures():
    """클래스 시드의 폐포 2종 — 없으면 동의어·하위클래스로 도달하던 인스턴스를 잃는다."""
    eq = "?c (owl:equivalentClass|^owl:equivalentClass)* ?ceq . "
    closure = q.seed_classes_by_fulltext_query(GRAPH, TERMS)          # 기본 = closure
    direct = q.seed_classes_by_fulltext_query(GRAPH, TERMS, "direct")
    assert eq in closure and eq in direct                              # 동치 폐포는 양쪽 공통
    assert "?sub rdfs:subClassOf* ?ceq . ?i rdf:type ?sub" in closure  # 이행 폐포는 closure 만
    assert "rdfs:subClassOf*" not in direct
    assert "?i rdf:type ?ceq . ?i rdfs:label ?il . BIND(?il AS ?dl) " in direct
    # ?directs — 150캡에서 폐포가 직접 인스턴스를 밀어내지 않도록 주입순서 고정용
    for got in (closure, direct):
        assert "(GROUP_CONCAT(DISTINCT ?dl; SEPARATOR=' | ') AS ?directs)" in got


def test_sealed_merge_journal_precedes_irreversible_move():
    """병합 저널 — DELETE/INSERT 물리 병합이 비가역이라 역추적 근거를 남긴다."""
    can = "https://w3id.org/xgen-instance#한국마사회"
    uri = can + "를"
    assert q.merge_journal_insert_update(GRAPH, can, uri, "한국마사회를") == (
        f"INSERT DATA {{ GRAPH <{GRAPH}__id_journal> {{ "
        f"<{can}> <{_NS}mergedFrom> <{uri}> . "
        f'<{uri}> <{_NS}mergedLabel> "한국마사회를"@ko }} }}')


def test_search_workload_contract_covers_both_chunk_slots():
    """워크로드 계약이 두 슬롯을 모두 요구해야 백엔드 스왑 시 결손이 드러난다."""
    from xgen_graphstore.capabilities import Workload, WORKLOAD_METHODS
    ms = WORKLOAD_METHODS[Workload.GRAPH_SEARCH]
    assert "seed_chunk_relations" in ms and "seed_chunk_cooccurrence" in ms


# ── 목-transport 파싱 등가 ──

class _Mock(FusekiBackend):
    def __init__(self, canned):
        super().__init__()
        self._canned = canned
        self.sent = []

    async def sparql_query(self, query):  # type: ignore[override]
        self.sent.append(query)
        return self._canned


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_node_properties_parse_equivalence():
    canned = {"results": {"bindings": [
        {"p": {"value": "https://w3id.org/xgen-domain#설립연도"},
         "pLabel": {"value": "설립연도"}, "o": {"value": "1969", "type": "literal"}},
        {"p": {"value": "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"},
         "o": {"value": "x", "type": "uri"}},
    ]}}
    b = _Mock(canned)
    props = _run(b.node_properties(GRAPH, NODE))
    assert props == [{"property": "설립연도", "property_uri": "https://w3id.org/xgen-domain#설립연도",
                      "value": "1969", "value_type": "literal"}]
    assert b.sent == [q.node_properties_query(GRAPH, NODE)]


def test_class_instance_counts_parse_equivalence():
    b = _Mock({"results": {"bindings": [
        {"classLabel": {"value": "회사"}, "instanceCount": {"value": "12"}},
        {"instanceCount": {"value": "3"}},
    ]}})
    assert _run(b.class_instance_counts(GRAPH)) == [
        {"class": "회사", "instance_count": 12},
        {"class": "", "instance_count": 3},
    ]


def test_write_methods_emit_sealed_queries():
    class _WMock(FusekiBackend):
        def __init__(self):
            super().__init__(); self.updates = []
        async def sparql_update(self, u):  # type: ignore[override]
            self.updates.append(u); return True
    b = _WMock()
    _run(b.insert_data(GRAPH, "<s> <p> <o> ."))
    _run(b.merge_move_subject(GRAPH, "u", "c"))
    _run(b.merge_move_object(GRAPH, "u", "c"))
    assert b.updates == [
        q.insert_data_update(GRAPH, "<s> <p> <o> ."),
        q.merge_move_subject_update(GRAPH, "u", "c"),
        q.merge_move_object_update(GRAPH, "u", "c"),
    ]


def test_factory_unknown_backend_raises():
    from xgen_graphstore import create_store, UnknownBackendError
    # 'fuseki'/'neo4j' 는 이제 지원 — 진짜 미지 백엔드만 명시적 에러(조용한 fallback 금지).
    with pytest.raises(UnknownBackendError):
        create_store({"backend": "cassandra"})
