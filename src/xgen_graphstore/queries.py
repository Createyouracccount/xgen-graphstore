# Provenance: xgen-documents service/ontology/fuseki_queries.py 무변경 이관. 출처 커밋 8a81e23.
# (순수 쿼리 빌더 — 내부 import 없음)
"""Fuseki SPARQL 쿼리 빌더 — 순수 함수 (골든 테스트 대상).

각 함수는 기존 인라인 f-string 이 만들던 SPARQL 문자열을 **바이트 동일**하게 재현한다.
이 함수들이 "정적 골든"의 비교 대상이다: 원본 인라인 vs 이 빌더 출력이 바이트 동일하면
동작 보존이 (쿼리 방출 측면에서) 증명된다.

⚠️ 절대 규칙: 쿼리 문자열을 "개선"·정리·정규화하지 말 것. 공백/개행/들여쓰기까지
원본과 1바이트도 다르면 안 된다. 원본 위치는 각 함수 docstring 에 명시.
"""

from __future__ import annotations


def node_properties_query(graph_name: str, node_uri: str) -> str:
    """원본: controller/ontology/endpoints/graph_rag_operations.py get_node_properties (~239-252)."""
    gc = f"GRAPH <{graph_name}>"
    return f"""
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX owl: <http://www.w3.org/2002/07/owl#>

    SELECT ?p ?pLabel ?o ?oLabel WHERE {{
        {gc} {{
            <{node_uri}> ?p ?o .
            OPTIONAL {{ ?p rdfs:label ?pLabel . FILTER(LANG(?pLabel) = "ko" || LANG(?pLabel) = "") }}
            OPTIONAL {{ ?o rdfs:label ?oLabel . FILTER(LANG(?oLabel) = "ko" || LANG(?oLabel) = "") }}
        }}
    }}
    LIMIT 50
    """


def property_values_query(graph_name: str, property_uri: str, limit: int) -> str:
    """원본: graph_rag_operations.py get_property_values (~292-302)."""
    gc = f"GRAPH <{graph_name}>"
    return f"""
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?instance ?instanceLabel ?value WHERE {{
        {gc} {{
            ?instance <{property_uri}> ?value .
            OPTIONAL {{ ?instance rdfs:label ?instanceLabel . FILTER(LANG(?instanceLabel) = "ko" || LANG(?instanceLabel) = "") }}
        }}
    }}
    LIMIT {min(limit, 50)}
    """


def neighbors_out_query(graph_name: str, node_uri: str) -> str:
    """원본: graph_rag_operations.py explore_node out_query (~416-434)."""
    gc = f"GRAPH <{graph_name}>"
    return f"""
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX owl: <http://www.w3.org/2002/07/owl#>

    SELECT ?p ?pLabel ?target ?targetLabel ?targetType WHERE {{
        {gc} {{
            <{node_uri}> ?p ?target .
            FILTER(?p != rdf:type && ?p != rdfs:label)
            OPTIONAL {{ ?p rdfs:label ?pLabel . FILTER(LANG(?pLabel) = "ko" || LANG(?pLabel) = "") }}
            OPTIONAL {{ ?target rdfs:label ?targetLabel . FILTER(LANG(?targetLabel) = "ko" || LANG(?targetLabel) = "") }}
            OPTIONAL {{
                ?target rdf:type ?cls . ?cls rdf:type owl:Class .
                ?cls rdfs:label ?targetType . FILTER(LANG(?targetType) = "ko" || LANG(?targetType) = "")
            }}
        }}
    }}
    LIMIT 100
    """


def neighbors_in_query(graph_name: str, node_uri: str) -> str:
    """원본: graph_rag_operations.py explore_node in_query (~437-455)."""
    gc = f"GRAPH <{graph_name}>"
    return f"""
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX owl: <http://www.w3.org/2002/07/owl#>

    SELECT ?source ?sourceLabel ?sourceType ?p ?pLabel WHERE {{
        {gc} {{
            ?source ?p <{node_uri}> .
            FILTER(?p != rdf:type)
            OPTIONAL {{ ?source rdfs:label ?sourceLabel . FILTER(LANG(?sourceLabel) = "ko" || LANG(?sourceLabel) = "") }}
            OPTIONAL {{ ?p rdfs:label ?pLabel . FILTER(LANG(?pLabel) = "ko" || LANG(?pLabel) = "") }}
            OPTIONAL {{
                ?source rdf:type ?cls . ?cls rdf:type owl:Class .
                ?cls rdfs:label ?sourceType . FILTER(LANG(?sourceType) = "ko" || LANG(?sourceType) = "")
            }}
        }}
    }}
    LIMIT 100
    """


def triple_exists_query(graph_name: str, s: str, p: str, o: str) -> str:
    """원본: graph_rag_operations.py _triple_exists ask_query (~526-531)."""
    return f"""
    ASK {{
        GRAPH <{graph_name}> {{
            <{s}> <{p}> <{o}> .
        }}
    }}
    """


def count_node_triples_query(graph_name: str, node_uri: str) -> str:
    """원본: graph_rag_operations.py _count_node_triples q (~770-775)."""
    return f"""
    SELECT (COUNT(*) AS ?c) WHERE {{
        GRAPH <{graph_name}> {{
            {{ <{node_uri}> ?p ?o }} UNION {{ ?s ?p2 <{node_uri}> }}
        }}
    }}
    """


# ── B2: graph_rag READ 집계 ──

def class_instance_counts_query(graph_name: str) -> str:
    """원본: graph_rag_operations.py abox_query (~1284-1299)."""
    return f"""
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX owl: <http://www.w3.org/2002/07/owl#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

    SELECT ?classLabel (COUNT(DISTINCT ?inst) AS ?instanceCount) WHERE {{
        GRAPH <{graph_name}> {{
            ?inst rdf:type owl:NamedIndividual .
            ?inst rdf:type ?cls .
            ?cls rdf:type owl:Class .
            ?cls rdfs:label ?classLabel .
            FILTER(LANG(?classLabel) = "ko" || LANG(?classLabel) = "")
        }}
    }} GROUP BY ?classLabel
    ORDER BY DESC(?instanceCount)
    """


def relation_triple_counts_query(graph_name: str) -> str:
    """원본: graph_rag_operations.py rel_query (~1309-1324)."""
    return f"""
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX owl: <http://www.w3.org/2002/07/owl#>
    PREFIX ns: <https://w3id.org/xgen-domain#>

    SELECT ?propLabel (COUNT(*) AS ?count) WHERE {{
        GRAPH <{graph_name}> {{
            ?s rdf:type owl:NamedIndividual .
            ?s ?p ?o .
            ?o rdf:type owl:NamedIndividual .
            FILTER(?p != rdf:type)
            OPTIONAL {{ ?p rdfs:label ?propLabel . FILTER(LANG(?propLabel) = "ko" || LANG(?propLabel) = "") }}
        }}
    }} GROUP BY ?propLabel
    ORDER BY DESC(?count)
    """


# ── B3: community_detect + multi_turn_rag ──
#
# 네임스페이스 상수는 원본(community_detect.py / multi_turn_rag.py)이 쓰던 리터럴과
# 바이트 동일해야 한다. 원본이 모듈 상수(NS/OWL/RDF/RDFS/NAMESPACE_URI)로 삽입하던 값.
_NS = "https://w3id.org/xgen-domain#"
_OWL = "http://www.w3.org/2002/07/owl#"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDFS = "http://www.w3.org/2000/01/rdf-schema#"


# community_detect READ
def community_edges_query(graph_name: str) -> str:
    """원본: community_detect.py q_edges (~100-103). 인스턴스 간 관계 간선."""
    G = graph_name
    RDF = _RDF
    OWL = _OWL
    return (
        f"SELECT ?s ?o WHERE {{ GRAPH <{G}> {{ "
        f"?s <{RDF}type> <{OWL}NamedIndividual> . ?s ?p ?o . ?o <{RDF}type> <{OWL}NamedIndividual> . "
        f"FILTER(?p != <{RDF}type>) }} }}")


def community_labels_query(graph_name: str) -> str:
    """원본: community_detect.py q_lab (~123-125). 인스턴스 라벨."""
    G = graph_name
    RDF = _RDF
    OWL = _OWL
    RDFS = _RDFS
    return (
        f"SELECT ?i ?l WHERE {{ GRAPH <{G}> {{ ?i <{RDF}type> <{OWL}NamedIndividual> ; <{RDFS}label> ?l . "
        f"FILTER(lang(?l) = 'ko' || lang(?l) = '') }} }}")


# community_detect WRITE (첫 write 이관 — DELETE→INSERT 순서·배치 보존)
def community_tag_delete_update(graph_name: str) -> str:
    """원본: community_detect.py sparql_update (~139-140). 기존 community 태그 삭제."""
    G = graph_name
    NS = _NS
    return f"WITH <{G}> DELETE {{ ?s <{NS}community> ?c }} WHERE {{ ?s <{NS}community> ?c }}"


def community_tag_insert_update(graph_name: str, triples: str) -> str:
    """원본: community_detect.py sparql_update (~146). 배치 community 태그 삽입.

    triples 는 호출부가 `" ".join(f"<{uri}> <{NS}community> {comm} ." ...)` 로 조립한 문자열.
    조립 로직은 도메인(comm_of)이라 호출부에 남고, 여기선 방출 문자열만 바이트 동일.
    """
    G = graph_name
    return f"INSERT DATA {{ GRAPH <{G}> {{ {triples} }} }}"


# multi_turn_rag READ — seed 쿼리 (일부 text:query=jena-text 전용, 부채는 원장 기록)
def _CHUNK_SEED_BASE(graph_name: str, values: str, projection: str) -> str:
    """원본: multi_turn_rag.py `_seed_chunk_neighborhood` 의 `base`. 바이트 동일이며
    projection 만 갈아끼운다
    (정본은 `base.replace("?sLabel ?pLabel ?oLabel", "?sLabel ?oLabel")` 로 같은 일을 한다)."""
    return (
        f"PREFIX : <{_NS}> "
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
        "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> "
        f"SELECT DISTINCT {projection} WHERE {{ GRAPH <{graph_name}> {{ "
        f"VALUES ?c {{ {values} }} "
        "?s :sourceChunk ?c . ?s rdfs:label ?sLabel . ?s ?p ?o . "
        "FILTER(?p != rdf:type && ?p != rdfs:label && ?p != :sourceChunk "
        "&& ?p != :sourceDocument && ?p != :scsContextSummary) "
    )


def seed_chunk_relations_query(graph_name: str, values: str, limit: int) -> str:
    """원본: multi_turn_rag.py gq (~211-222). VALUES 바인딩 1홉 관계 시드.

    values 는 호출부가 `" ".join(f'"{c}"' ...)` 로 조립한 청크 리터럴 목록.
    (VALUES 바인딩은 LPG 파라미터 리스트로 자연 이식 — 부채 아님.)

    ⚠️ 0824 전방이식: `FILTER(?p != :coOccursWith)` 가 빠져 있었다. 정본은 정밀 SVO 와
    동시출현 약관계를 **슬롯 분리**한다 — 단일 정렬 LIMIT 는 SVO 가 항상 선점해
    co-occ 가 0 이 되기 때문(mixed20k 실측). 약관계는 `seed_chunk_cooccurrence_query`.
    """
    return (
        _CHUNK_SEED_BASE(graph_name, values, "?sLabel ?pLabel ?oLabel")
        + "FILTER(?p != :coOccursWith) "
        "?o rdfs:label ?oLabel . OPTIONAL { ?p rdfs:label ?pLabel } } } "
        f"LIMIT {limit}"
    )


def seed_chunk_cooccurrence_query(graph_name: str, values: str, limit: int) -> str:
    """원본: multi_turn_rag.py `cq` (_seed_chunk_neighborhood). 동시출현 약관계 슬롯.

    정밀 SVO(`seed_chunk_relations_query`) 와 **별도 소량 슬롯**으로 뒤에 append 한다.
    술어가 `:coOccursWith` 단일이라 `?pLabel` 을 조회하지 않는다 — ko/en 라벨 2행 중복으로
    슬롯 절반이 낭비되는 것을 막기 위함이며, 표기는 호출부에서 "함께언급" 으로 고정한다.

    limit 은 호출부가 `max(10, SEED_REL_LIMIT // 5)` 로 계산해 넘긴다(빌더는 방출만).
    """
    return (
        _CHUNK_SEED_BASE(graph_name, values, "?sLabel ?oLabel")
        + "FILTER(?p = :coOccursWith) ?o rdfs:label ?oLabel . } } "
        f"LIMIT {limit}"
    )


def predicate_labels_query(graph_name: str) -> str:
    """원본: multi_turn_rag.py _pred_labels (~504-505). 술어 라벨 목록(순수 SPARQL)."""
    return (
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
        f"SELECT DISTINCT ?p ?pl WHERE {{ GRAPH <{graph_name}> {{ ?s ?p ?o . ?p rdfs:label ?pl }} }}"
    )


# multi_turn 술어 필터·PREFIX (원본 클래스 상수와 바이트 동일)
_PRED_FILTER = ("FILTER(?p != rdf:type && ?p != rdfs:label && ?p != :sourceChunk "
                "&& ?p != :sourceDocument && ?p != :scsContextSummary) ")
_SEED_PFX = (
    f"PREFIX : <{_NS}> "
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
    "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> "
    "PREFIX text: <http://jena.apache.org/text#> "
)


def seed_relations_by_fulltext_forward_query(graph_name: str, terms: str, pin: str) -> str:
    """원본: multi_turn_rag.py qf (~551-554). ⚠️text:query(jena-text) — 부채."""
    pfx = "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> PREFIX text: <http://jena.apache.org/text#> "
    return (pfx + "SELECT DISTINCT ?sl ?pl ?ol WHERE { "
            f'(?s ?sc) text:query (rdfs:label "{terms}" 15) . '
            f"GRAPH <{graph_name}> {{ ?s rdfs:label ?sl . ?s ?p ?o . ?p rdfs:label ?pl . FILTER(STR(?pl) IN ({pin})) "
            "OPTIONAL { ?o rdfs:label ?ol2 } BIND(COALESCE(STR(?ol2), STR(?o)) AS ?ol) } } LIMIT 40")


def seed_relations_by_fulltext_reverse_query(graph_name: str, terms: str, pin: str) -> str:
    """원본: multi_turn_rag.py qr (~556-559). ⚠️text:query(jena-text) — 부채."""
    pfx = "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> PREFIX text: <http://jena.apache.org/text#> "
    return (pfx + "SELECT DISTINCT ?sl ?pl ?ol WHERE { "
            f'(?o ?sc) text:query (rdfs:label "{terms}" 15) . '
            f"GRAPH <{graph_name}> {{ ?o rdfs:label ?ol . ?s ?p ?o . ?p rdfs:label ?pl . FILTER(STR(?pl) IN ({pin})) "
            "?s rdfs:label ?sl } } LIMIT 40")


def seed_connectivity_relations_query(graph_name: str, terms: str, limit: int) -> str:
    """원본: multi_turn_rag.py q_conn (~596-604). ⚠️text:query(jena-text) — 부채."""
    return (
        _SEED_PFX + "SELECT DISTINCT ?sLabel ?pLabel ?oLabel WHERE { "
        f'(?s ?sc1) text:query (rdfs:label "{terms}" 80) . '
        f'(?o ?sc2) text:query (rdfs:label "{terms}" 80) . '
        f"GRAPH <{graph_name}> {{ ?s rdfs:label ?sLabel . ?s ?p ?o . ?o rdfs:label ?oLabel . "
        "FILTER(?s != ?o) " + _PRED_FILTER +
        # 0824 전방이식: 정밀 시드에선 동시출현 약관계를 제외한다 — coarse 엣지의
        # LIMIT 선점 방지. (broad recall 폴백에는 포함 — SVO 없는 희소 구간을 메꾼다)
        "FILTER(?p != :coOccursWith) "
        "OPTIONAL { ?p rdfs:label ?pLabel } } } "
        f"LIMIT {limit}"
    )


def seed_relations_broad_query(graph_name: str, terms: str, limit: int) -> str:
    """원본: multi_turn_rag.py q_broad (~612-619). ⚠️text:query(jena-text) — 부채."""
    return (
        _SEED_PFX + "SELECT DISTINCT ?sLabel ?pLabel ?oLabel WHERE { "
        f'(?s ?sc) text:query (rdfs:label "{terms}" 60) . '
        f"GRAPH <{graph_name}> {{ ?s rdfs:label ?sLabel . ?s ?p ?o . "
        + _PRED_FILTER +
        "?o rdfs:label ?oLabel . OPTIONAL { ?p rdfs:label ?pLabel } } } "
        f"LIMIT {limit}"
    )


def seed_classes_by_fulltext_query(graph_name: str, terms: str,
                                   mode: str = "closure") -> str:
    """원본: multi_turn_rag.py `_seed_classes.q`. ⚠️text:query(jena-text) — 부채.

    ⚠️ 0824 전방이식. 추출 당시 판본은 `?i rdf:type ?c` 뿐이었고, 정본이 그 뒤 채택한
    폐포 2종과 `?directs` 컬럼이 빠져 있었다:

    - **동치 폐포(R9)**: `?c (owl:equivalentClass|^owl:equivalentClass)* ?ceq` —
      클래스 동의어 정규화가 넣은 링크를 양방향으로 따라간다('국가' 해소가 '나라'
      클래스 인스턴스까지 도달, 실측 11→16). 링크가 없으면 zero-length path 라 무변경.
    - **이행 폐포**: `?sub rdfs:subClassOf* ?ceq` — 매칭 클래스의 하위클래스 인스턴스까지
      전수 포함. `*` 는 zero-length 포함이라 flat 온톨로지에선 순수 superset.
    - **`?directs`**: 직접 타입 인스턴스를 따로 모은다. 150캡에서 폐포 인스턴스가 직접
      인스턴스를 밀어내는 기계적 간섭을 막기 위해 주입 순서를 결정론으로 고정하는 용도.

    mode 는 호출부 env `ONTOLOGY_CLASS_SEED` 를 그대로 받는다.
    `"closure"`(운영 컨테이너 설정) / `"direct"`(폐포 없이 직접 타입만, A/B 계측용).
    `"off"` 는 호출 자체를 하지 않는 것이므로 여기서 다루지 않는다.
    """
    eq = "?c (owl:equivalentClass|^owl:equivalentClass)* ?ceq . "
    if mode == "direct":
        type_path = eq + "?i rdf:type ?ceq . ?i rdfs:label ?il . BIND(?il AS ?dl) "
    else:
        type_path = (eq + "?sub rdfs:subClassOf* ?ceq . ?i rdf:type ?sub . ?i rdfs:label ?il . "
                     "OPTIONAL { ?i rdf:type ?ceq . BIND(?il AS ?dl) } ")
    return (
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
        "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> "
        "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
        "PREFIX text: <http://jena.apache.org/text#> "
        "SELECT (SAMPLE(?cll) AS ?cl) (COUNT(DISTINCT ?i) AS ?n) "
        "(GROUP_CONCAT(DISTINCT ?il; SEPARATOR=' | ') AS ?insts) "
        "(GROUP_CONCAT(DISTINCT ?dl; SEPARATOR=' | ') AS ?directs) WHERE { "
        f'(?c ?sc) text:query (rdfs:label "{terms}" 30) . '
        f"GRAPH <{graph_name}> {{ ?c rdf:type owl:Class . ?c rdfs:label ?cll . "
        + type_path + "} } "
        "GROUP BY ?c ORDER BY DESC(?n) LIMIT 3"
    )


# ── B4: graph_rag WRITE ──
#
# ⚠️ 멱등성 계약: INSERT/DELETE 의 ASK 사전확인·사후검증(_triple_exists)은 호출부에
# 그대로 남는다(B1에서 triple_exists 는 이미 인터페이스 경유). 여기선 방출 문자열만.
# triple 라인 조립(도메인: IRI/리터럴 이스케이프·edge_kind 확장)은 호출부에 남긴다.

def insert_data_update(graph_name: str, triple_lines: str) -> str:
    """원본: _insert_triples update_query (graph_rag_operations.py ~436-442).

    triple_lines 는 호출부가 조립한 `' '.join(lines)` (IRI + 이스케이프 리터럴).
    """
    return f"""
    INSERT DATA {{
        GRAPH <{graph_name}> {{
            {triple_lines}
        }}
    }}
    """


def delete_data_update(graph_name: str, triple_lines: str) -> str:
    """원본: _delete_triples update_query (graph_rag_operations.py ~542-548).

    triple_lines 는 호출부가 조립한 `" ".join(f"<{s}> <{p}> <{o}> ." ...)`.
    """
    return f"""
    DELETE DATA {{
        GRAPH <{graph_name}> {{
            {triple_lines}
        }}
    }}
    """


def delete_node_subject_update(graph_name: str, node_uri: str) -> str:
    """원본: delete_node subject-측 (graph_rag_operations.py ~656-657)."""
    return f"DELETE WHERE {{ GRAPH <{graph_name}> {{ <{node_uri}> ?p ?o }} }}"


def delete_node_object_update(graph_name: str, node_uri: str) -> str:
    """원본: delete_node object-측 (graph_rag_operations.py ~659-660)."""
    return f"DELETE WHERE {{ GRAPH <{graph_name}> {{ ?s ?p <{node_uri}> }} }}"


# ── B5: pipeline 병합/rename (HARD — 2면 triple 이동, WITH <g> 스코프) ──
#
# ⚠️ 병합/rename 은 주어면(subject) + 목적어면(object) 각각 DELETE/INSERT 이동.
# 이동 순서(주어 먼저, 목적어 나중)·WITH <graph> 스코프 바이트 보존.
# canonical/uri/label 선정 로직은 도메인(호출부)에 남기고, 여기선 방출 문자열만.

def merge_move_subject_update(graph_name: str, uri: str, canonical: str) -> str:
    """원본: _merge_* 주어면 이동 (pipeline.py ~2772-2776 / 2827-2831)."""
    return (
        f"WITH <{graph_name}> "
        f"DELETE {{ <{uri}> ?p ?o }} INSERT {{ <{canonical}> ?p ?o }} "
        f"WHERE {{ <{uri}> ?p ?o }}"
    )


def merge_move_object_update(graph_name: str, uri: str, canonical: str) -> str:
    """원본: _merge_* 목적어면 이동 (pipeline.py ~2777-2781 / 2832-2836)."""
    return (
        f"WITH <{graph_name}> "
        f"DELETE {{ ?s ?p <{uri}> }} INSERT {{ ?s ?p <{canonical}> }} "
        f"WHERE {{ ?s ?p <{uri}> }}"
    )


def merge_normalized_instances_select(graph_name: str) -> str:
    """원본: _merge_normalized_instances SELECT (pipeline.py ~2737-2740)."""
    return (
        'PREFIX owl: <http://www.w3.org/2002/07/owl#> '
        'PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> '
        f'SELECT ?i ?l WHERE {{ GRAPH <{graph_name}> {{ '
        '?i a owl:NamedIndividual ; rdfs:label ?l . FILTER(lang(?l) = "ko") } }'
    )


def merge_journal_insert_update(graph_name: str, canonical: str, uri: str,
                                label_escaped: str) -> str:
    """원본: pipeline.py `_merge_normalized_instances` 병합 저널 사이드카 (0824 전방이식).

    DELETE/INSERT 물리 병합은 **비가역**이라, `(canonical, mergedFrom, old)` 와 옛 라벨을
    `<graph>__id_journal` 별도 그래프에 남겨 감사·역추적을 가능하게 한다.
    추출 당시 판본엔 없었다 — 병합 이동(`merge_move_*`)만 이관돼, 이 빌더 없이 스왑하면
    **저널이 조용히 사라진다**(실서버 Fuseki 에 해당 그래프 실재 확인, 0824).

    label_escaped 는 호출부가 이스케이프한 리터럴 본문이다 — 이 모듈의 계약상
    IRI/리터럴 이스케이프는 도메인(호출부)에 남긴다.
    """
    return (
        f"INSERT DATA {{ GRAPH <{graph_name}__id_journal> {{ "
        f"<{canonical}> <{_NS}mergedFrom> <{uri}> . "
        f'<{uri}> <{_NS}mergedLabel> "{label_escaped}"@ko }} }}'
    )


def merge_same_label_select(graph_name: str, rdfs: str, type_uri: str) -> str:
    """원본: _merge_same_label_nodes SELECT (pipeline.py ~2807-2809)."""
    return (
        f'PREFIX rdfs: <{rdfs}> '
        f'SELECT ?c ?l WHERE {{ GRAPH <{graph_name}> {{ '
        f'?c a <{type_uri}> ; rdfs:label ?l . FILTER(lang(?l) = "ko") }} }}'
    )


def rename_move_subject_update(graph_name: str, rdfs: str, ol: str, nl: str) -> str:
    """원본: _apply_rename_to_graph 주어면 (pipeline.py ~2894-2898)."""
    return (
        f'PREFIX rdfs: <{rdfs}> WITH <{graph_name}> '
        f'DELETE {{ ?old ?p ?o }} INSERT {{ ?new ?p ?o }} '
        f'WHERE {{ ?old rdfs:label "{ol}"@ko . ?new rdfs:label "{nl}"@ko . '
        f'FILTER(?old != ?new) . ?old ?p ?o }}'
    )


def rename_move_object_update(graph_name: str, rdfs: str, ol: str, nl: str) -> str:
    """원본: _apply_rename_to_graph 목적어면 (pipeline.py ~2901-2905)."""
    return (
        f'PREFIX rdfs: <{rdfs}> WITH <{graph_name}> '
        f'DELETE {{ ?s ?p ?old }} INSERT {{ ?s ?p ?new }} '
        f'WHERE {{ ?old rdfs:label "{ol}"@ko . ?new rdfs:label "{nl}"@ko . '
        f'FILTER(?old != ?new) . ?s ?p ?old }}'
    )


def rename_drop_old_label_update(graph_name: str, rdfs: str, ol: str, nl: str) -> str:
    """원본: _apply_rename_to_graph old 라벨 제거 (pipeline.py ~2908-2912)."""
    return (
        f'PREFIX rdfs: <{rdfs}> WITH <{graph_name}> '
        f'DELETE {{ ?new rdfs:label "{ol}"@ko }} '
        f'WHERE {{ ?new rdfs:label "{nl}"@ko . ?new rdfs:label "{ol}"@ko }}'
    )
