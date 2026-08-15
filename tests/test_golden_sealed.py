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
    with pytest.raises(UnknownBackendError):
        create_store({"backend": "neo4j"})
