"""Neo4j 검색 이식분 — 리터럴 저장 + 반환형 계약 (DEBTS §A 착수순서 1·2). 서버 불필요."""

import pytest

from xgen_graphstore.neo4j_backend import (
    Neo4jBackend, _localname, _parse_literals, _parse_triples,
)

RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
SRC_CHUNK = "https://w3id.org/xgen-domain#sourceChunk"


def test_literal_parsed_as_property_not_edge():
    """RDF 리터럴은 엣지가 아니라 노드 property 로 간다 — 리소스 트리플과 분리 파싱."""
    line = f'<http://x/a> <{RDFS_LABEL}> "한국은행" .\n'
    assert _parse_triples(line) == [], "리터럴은 리소스 트리플로 잡히면 안 됨"
    assert _parse_literals(line) == [{"s": "http://x/a", "key": "label", "val": "한국은행"}]


def test_literal_escapes_and_lang_tag():
    """이스케이프(\\")·언어태그(@ko)·데이터타입(^^<>)을 실제 N-Triples 형태로 처리."""
    assert _parse_literals('<http://x/a> <http://x/p> "a \\" b" .')[0]["val"] == 'a " b'
    assert _parse_literals('<http://x/a> <http://x/p> "값"@ko .')[0]["val"] == "값"
    assert _parse_literals(
        '<http://x/a> <http://x/p> "7"^^<http://www.w3.org/2001/XMLSchema#int> .'
    )[0]["val"] == "7"


def test_unsafe_property_key_skipped_not_silently_stored():
    """키로 부적합한 술어는 건너뛴다 — 엉뚱한 키로 조용히 저장되는 것 방지."""
    assert _parse_literals('<http://x/a> <http://x/has-dash> "v" .') == []


def test_mixed_lines_split_correctly():
    """관계와 리터럴이 섞인 입력에서 각각 정확히 분리된다."""
    lines = (
        '<http://x/a> <http://x/rel> <http://x/b> .\n'
        f'<http://x/a> <{RDFS_LABEL}> "에이" .\n'
        f'<http://x/a> <{SRC_CHUNK}> "c1" .\n'
    )
    assert len(_parse_triples(lines)) == 1
    lits = _parse_literals(lines)
    assert {l["key"] for l in lits} == {"label", "sourceChunk"}


def test_bindings_shape_matches_sparql_json():
    """반환형이 Fuseki 와 동일해야 호출부(multi_turn_rag) 파싱이 그대로 동작한다."""
    out = Neo4jBackend._bindings([{"sLabel": "A", "pLabel": "rel", "oLabel": "B"}])
    assert out == {"results": {"bindings": [
        {"sLabel": {"type": "literal", "value": "A"},
         "pLabel": {"type": "literal", "value": "rel"},
         "oLabel": {"type": "literal", "value": "B"}}]}}


def test_bindings_omits_none_like_sparql_optional():
    """None 은 키 자체를 생략 — SPARQL OPTIONAL 미바인딩과 등가."""
    b = Neo4jBackend._bindings([{"sLabel": "A", "pLabel": None}])["results"]["bindings"][0]
    assert "pLabel" not in b and b["sLabel"]["value"] == "A"


def test_parse_values_reads_caller_format():
    """호출부가 만든 VALUES 리터럴 목록(`\"a\" \"b\"`)을 그대로 읽는다."""
    assert Neo4jBackend._parse_values('"c1" "c2" "c3"') == ["c1", "c2", "c3"]
    assert Neo4jBackend._parse_values("") == []


def test_localname_for_predicate_label():
    """LPG 에서 술어 라벨 등가물 = URI localname."""
    assert _localname("https://w3id.org/xgen-domain#coOccursWith") == "coOccursWith"
    assert _localname("http://example.org/path/rel") == "rel"


def test_multi_valued_label_not_collapsed():
    """RDF 는 같은 술어에 값이 여럿일 수 있다 — 단일값 취급하면 조용히 덮어써진다.

    실측 회귀: coOccursWith 의 rdfs:label 이 "함께언급"·"co-occurs with" 둘 다 존재했는데
    단일값으로 저장해 나중 값이 앞 값을 덮었고, 검색 결과 149건이 소리 없이 누락됐다.
    파서 단계에서 두 값이 **모두 살아 있어야** 한다(적재 단계는 리스트로 누적).
    """
    lines = (
        f'<http://x/p> <{RDFS_LABEL}> "함께언급" .\n'
        f'<http://x/p> <{RDFS_LABEL}> "co-occurs with" .\n'
    )
    lits = _parse_literals(lines)
    assert len(lits) == 2, "다중 label 이 파싱 단계에서 합쳐지면 안 됨"
    assert {l["val"] for l in lits} == {"함께언급", "co-occurs with"}
    assert all(l["key"] == "label" for l in lits)
