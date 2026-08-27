"""Neo4j 검색 이식분 — 리터럴 저장 + 반환형 계약 (DEBTS §A 착수순서 1·2). 서버 불필요."""

import pytest

from xgen_graphstore.neo4j_backend import (
    Neo4jBackend, _localname, _parse_literals, _parse_triples,
    _group_literals_by_key,
)

RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
SRC_CHUNK = "https://w3id.org/xgen-domain#sourceChunk"


def test_literal_parsed_as_property_not_edge():
    """RDF 리터럴은 엣지가 아니라 노드 property 로 간다 — 리소스 트리플과 분리 파싱."""
    line = f'<http://x/a> <{RDFS_LABEL}> "한국은행" .\n'
    assert _parse_triples(line) == [], "리터럴은 리소스 트리플로 잡히면 안 됨"
    assert _parse_literals(line) == [{
        "s": "http://x/a", "key": "label", "val": "한국은행",
        # 0827: 파서가 더는 버리지 않는다. key(localname)는 손실 축약이고 p 가 원본이다.
        "p": RDFS_LABEL, "lang": "", "dtype": "",
    }]


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


# ── fulltext 이식 (DEBTS §A 4단계) ──

def test_lucene_escape_blocks_query_syntax_injection():
    """사용자 입력이 Lucene 질의 문법으로 해석되면 안 된다."""
    from xgen_graphstore.neo4j_backend import _lucene_escape
    assert _lucene_escape("a+b") == r"a\+b"
    assert _lucene_escape('label:"x" OR *') == r'label\:\"x\" OR \*'
    assert _lucene_escape("한국은행") == "한국은행", "한글은 그대로"


def test_parse_pin_reads_caller_predicate_list():
    """술어 핀은 호출부가 '\"a\", \"b\"' 로 조립한다 — 원본 FILTER(STR(?pl) IN (...)) 등가."""
    from xgen_graphstore.neo4j_backend import _parse_pin
    assert _parse_pin('"org:alternate_names", "함께언급"') == ["org:alternate_names", "함께언급"]
    assert _parse_pin("") == []


def test_excluded_predicates_match_original_filter():
    """원본 _PRED_FILTER 와 같은 술어를 제외해야 결과가 어긋나지 않는다."""
    from xgen_graphstore.neo4j_backend import _EXCLUDED_PREDS
    tails = {p.rsplit("#", 1)[-1] for p in _EXCLUDED_PREDS}
    assert tails == {"type", "label", "sourceChunk", "sourceDocument", "scsContextSummary"}


def test_fulltext_capability_declared():
    """fulltext 이식 후 능력 선언이 실제 구현과 일치해야 한다(거짓 선언 금지)."""
    from xgen_graphstore.capabilities import Capability
    from xgen_graphstore.neo4j_backend import Neo4jBackend
    assert Capability.FULLTEXT_SEARCH in Neo4jBackend.CAPABILITIES
    for m in ("seed_connectivity_relations", "seed_relations_broad", "seed_classes_by_fulltext",
              "seed_relations_by_fulltext_forward", "seed_relations_by_fulltext_reverse"):
        assert callable(getattr(Neo4jBackend, m, None)), f"{m} 미구현인데 능력만 선언하면 안 됨"


def test_ensure_schema_creates_uri_constraint():
    """uri 유니크 제약(=인덱스) 보장이 계약에 있어야 한다 — 없으면 MERGE 가 풀스캔(122배 느림)."""
    import inspect
    from xgen_graphstore.neo4j_backend import Neo4jBackend
    src = inspect.getsource(Neo4jBackend.ensure_schema)
    assert "CREATE CONSTRAINT" in src and "IS UNIQUE" in src
    assert "n.uri" in src, "uri 프로퍼티에 제약이 걸려야 MERGE 가 인덱스를 탄다"


def test_insert_data_ensures_schema_first():
    """적재 경로가 스키마 보장을 호출해야 한다(인덱스 없이 적재되는 것 방지)."""
    import inspect
    from xgen_graphstore.neo4j_backend import Neo4jBackend
    src = inspect.getsource(Neo4jBackend.insert_data)
    assert "_ensure_schema_once" in src


# ── 온톨로지 빌드 경로 (kg_builder.build_and_upload) ──

def test_build_workload_methods_exist():
    """빌드 핵심 경로(ensure_dataset→clear_graph→upload_ttl→정제2종→count)가 구현돼야 한다."""
    from xgen_graphstore.neo4j_backend import Neo4jBackend
    for m in ("ensure_dataset", "clear_graph", "upload_ttl",
              "clean_subclassof_noise", "materialize_property_inheritance",
              "get_triple_count"):
        assert callable(getattr(Neo4jBackend, m, None)), f"{m} 미구현 — 빌드가 여기서 죽는다"


def test_upload_ttl_requires_rdflib_loudly():
    """rdflib 부재를 조용히 넘기지 않는다 — 빌드가 무증상으로 비면 안 된다."""
    import inspect
    from xgen_graphstore.neo4j_backend import Neo4jBackend
    src = inspect.getsource(Neo4jBackend.upload_ttl)
    assert "RuntimeError" in src and "rdflib" in src


def test_triple_count_scopes_literals_to_graph():
    """리터럴 집계가 그래프 범위를 타야 한다.

    회귀: 초기 구현이 전체 노드를 훑어 1,202 트리플이 15,004 로 12배 과다 계상됐다.
    """
    import inspect
    from xgen_graphstore.neo4j_backend import Neo4jBackend
    src = inspect.getsource(Neo4jBackend.get_triple_count)
    # graph_name 이 주어진 분기에서 리터럴도 그 그래프의 엣지에 참여한 노드로 좁혀야 한다
    assert "REL {g: $g}]-() WITH DISTINCT n" in src.replace("\n", " ").replace("  ", " ") \
        or "-[:REL {g: $g}]-()" in src


def test_owl_cleanup_uses_property_type_gate():
    """subClassOf 정제·상속은 '부모가 속성으로 타입된 경우 제외' 게이트를 지켜야 한다."""
    import inspect
    from xgen_graphstore.neo4j_backend import Neo4jBackend
    for m in (Neo4jBackend.clean_subclassof_noise, Neo4jBackend.materialize_property_inheritance):
        assert "_PROPERTY_TYPES" in inspect.getsource(m) or "prop_types" in inspect.getsource(m)


# ── 리터럴 모델 손실 재현 (DEBTS §D-2) — 백엔드 공통 파서 계약 ──

def test_literal_keeps_predicate_uri():
    """localname 만 남기면 서로 다른 네임스페이스의 같은 이름이 한 칸에 뭉친다.

    실사고 맥락: `node_properties` 는 `?p` 를 **URI 로** 돌려줘야 하는데, localname 에서
    `NS_DOMAIN + key` 로 되살리는 것은 네임스페이스 추측이라 '회색지대 기본값 금지' 위반이다.
    """
    lines = (
        '<http://x/a> <https://w3id.org/xgen-domain#note> "가" .\n'
        '<http://x/a> <http://other.example/ns#note> "나" .\n'
    )
    lits = _parse_literals(lines)
    assert {l["p"] for l in lits} == {
        "https://w3id.org/xgen-domain#note", "http://other.example/ns#note"
    }, "술어 URI 가 보존돼야 한다 — key(localname) 만으로는 두 네임스페이스가 구분 불가"


def test_literal_keeps_language_tag():
    """원본 browse 질의는 전부 FILTER(LANG(?x)="ko" || LANG(?x)="") 를 쓴다.

    실측 사고: coOccursWith 의 rdfs:label 이 "함께언급"(ko)·"co-occurs with"(en) 둘 다인데
    언어 정보가 없어 화면에 영어 라벨이 섞여 나온다 — 조용히 다른 결과.
    """
    lines = (
        f'<http://x/a> <{RDFS_LABEL}> "함께언급"@ko .\n'
        f'<http://x/a> <{RDFS_LABEL}> "co-occurs with"@en .\n'
        f'<http://x/a> <{RDFS_LABEL}> "무태그" .\n'
    )
    got = {(l["val"], l["lang"]) for l in _parse_literals(lines)}
    assert got == {("함께언급", "ko"), ("co-occurs with", "en"), ("무태그", "")}, (
        "언어태그가 보존돼야 ko/'' 필터를 재현할 수 있다"
    )


def test_literal_keeps_datatype():
    """같은 정규식이 같은 방식으로 버리던 세 번째 항목 — 보존만 하고 소비자는 아직 없다."""
    line = '<http://x/a> <http://x/p> "7"^^<http://www.w3.org/2001/XMLSchema#int> .'
    lit = _parse_literals(line)[0]
    assert lit["dtype"] == "http://www.w3.org/2001/XMLSchema#int"
    assert lit["lang"] == "", "데이터타입 리터럴에 언어태그는 없다(RDF 상 배타)"


def test_group_literals_by_key_does_not_re_drop_metadata():
    """파서가 보존해도 배치 묶기에서 다시 버리면 수리가 무의미해진다."""
    lines = (
        f'<http://x/a> <{RDFS_LABEL}> "함께언급"@ko .\n'
        f'<http://x/a> <{RDFS_LABEL}> "co-occurs with"@en .\n'
    )
    batch = _group_literals_by_key(_parse_literals(lines))["label"]
    assert {(r["v"], r["lang"]) for r in batch} == {("함께언급", "ko"), ("co-occurs with", "en")}
    assert all(r["p"] == RDFS_LABEL for r in batch)
