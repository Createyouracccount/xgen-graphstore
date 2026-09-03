"""Neo4jBackend — LPG(Cypher) 백엔드 **PoC**.

목적: "graphstore 만 갈아끼우면 RDF↔LPG 스왑이 된다"를 실제 통과하는 테스트로 증명한다.
FusekiBackend 와 **같은 OntologyStore 계약 시그니처/반환형**을 지키되, 내부는 SPARQL 이
아니라 Cypher 다. documents 는 이 파일의 존재조차 몰라도 된다(create_store 로만 선택).

── 스코프(정직 공시) ──
이 PoC 는 **리소스-대-리소스 트리플의 핵심 6메서드**만 구현한다:
  insert_data · delete_data · triple_exists · count_node_triples ·
  merge_move_subject · merge_move_object  (+ health_check/close)
이것으로 B4 write 왕복 + B5 병합 왕복(구 URI 잔존 0)을 Fuseki 와 동일하게 통과시킨다.

미구현(NotImplementedError → DEBTS.md 의 3층 H 항목):
  text:query 전문검색 5종, OWL/RDFS-as-data, named-graph staging/control,
  literal 속성(node_properties/property_values), TTL 업로드 등.
이들은 LPG 재모델링이 필요한 "진짜 대공사"이며 **전부 graphstore 안**에서 처리된다(documents 무관).

── RDF ↔ LPG 매핑(PoC) ──
  트리플 (s, p, o) @ named-graph g  →  (:Resource {uri:s})-[:REL {p:p, g:g}]->(:Resource {uri:o})
  count_node_triples = out-degree + in-degree (트리플에서 노드가 주어∪목적어인 수)
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

from xgen_graphstore.capabilities import Capability, METHOD_CAPABILITY

# N-Triples 파싱은 백엔드 공통(ntriples 모듈) — 백엔드마다 다르게 파싱하면 같은 입력이
# 백엔드별로 다른 그래프가 되어 스왑 계약이 깨진다. 아래는 기존 이름 유지를 위한 얇은 별칭.
from xgen_graphstore.ntriples import (  # noqa: E402
    NS_DOMAIN as _NS_DOMAIN,
    group_literals_by_key as _group_literals_by_key,
    localname as _localname,
    parse_literals as _parse_literals,
    unescape as _unescape,
)

# ⚠️ 단일값 특례를 두지 않는다. RDF 는 같은 술어에 값이 여럿일 수 있고(실데이터에서
# coOccursWith 의 rdfs:label 이 "함께언급"·"co-occurs with" 둘 다 존재), 단일값으로 다루면
# 나중 값이 앞 값을 **조용히 덮어써** 검색 결과가 소리 없이 누락된다(실측서 149건 소실).
# 전부 리스트로 누적하고, 읽는 쪽이 SPARQL 처럼 값마다 행을 펼친다.


def _parse_triples(triple_lines: str) -> List[dict]:
    from xgen_graphstore.ntriples import parse_triples

    return [{"s": s, "p": p, "o": o} for (s, p, o) in parse_triples(triple_lines)]


# RDF 어휘·검색 공통 상수는 ntriples 모듈이 단일 출처(백엔드별로 갈라지면 결과가 어긋난다).
from xgen_graphstore.ntriples import (  # noqa: E402
    EXCLUDED_PREDS as _EXCLUDED_PREDS,
    OWL_CLASS as _OWL_CLASS,
    OWL_EQUIVALENT_CLASS as _OWL_EQ_CLASS,
    PROPERTY_TYPES as _PROPERTY_TYPES,
    RDF_TYPE as _RDF_TYPE,
    RDFS_DOMAIN as _RDFS_DOMAIN,
    RDFS_SUBCLASS as _RDFS_SUBCLASS,
    parse_pin as _parse_pin,
)

# Lucene 질의 특수문자 — 이스케이프하지 않으면 사용자 입력이 질의 문법으로 해석된다.
_LUCENE_SPECIAL = r'+-&|!(){}[]^"~*?:\/'


def _lucene_escape(terms: str) -> str:
    out = []
    for ch in terms:
        if ch in _LUCENE_SPECIAL:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)



_COOC_URI = _NS_DOMAIN + "coOccursWith"


class Neo4jBackend:
    """OntologyStore 계약의 LPG 구현(PoC). FusekiBackend 와 동일 시그니처."""

    BACKEND_NAME = "neo4j"
    # 리소스-트리플 CRUD + GDS 그래프알고리즘(§13) + fulltext 검색(DEBTS §A, Lucene cjk analyzer).
    # named-graph/owl-as-data/ttl/raw 는 미보유(DEBTS H).
    # OWL_SCHEMA: subClassOf 정제·상속 구체화만 이식(빌드 경로 필수분).
    # get_tbox_schema / count_classes 등 나머지 OWL-as-data 는 아직 미구현이라
    # 능력을 선언하면 **거짓 선언**이 된다 → METHOD_CAPABILITY 매핑에서 개별 차단되도록 두고,
    # 여기서는 선언하지 않는다. (능력 단위가 메서드보다 굵어 생기는 한계 — DEBTS §D)
    CAPABILITIES = frozenset({
        Capability.CORE_TRIPLE_RW,
        Capability.GRAPH_ALGORITHMS,
        Capability.FULLTEXT_SEARCH,
    })

    def __init__(
        self,
        uri: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
    ) -> None:
        from neo4j import AsyncGraphDatabase  # 지연 import — 코어 설치에 neo4j 불필요

        self._uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self._user = username or os.getenv("NEO4J_USER", "neo4j")
        self._password = password or os.getenv("NEO4J_PASSWORD", "neo4j")
        self._database = database or os.getenv("NEO4J_DATABASE", "neo4j")
        self._driver = AsyncGraphDatabase.driver(
            self._uri, auth=(self._user, self._password)
        )

    async def close(self) -> None:
        await self._driver.close()

    async def ensure_schema(self) -> bool:
        """uri 유니크 제약(=인덱스) 보장. **적재 성능의 핵심.**

        ⚠️ 이게 없으면 `MERGE (n:Resource {uri: ...})` 가 매번 전체 노드를 훑는다(풀스캔).
        실측: 인덱스 없이 17,094줄 적재에 588.6초 — 같은 데이터를 ArcadeDB(uri 인덱스 보유)는
        4.8초에 끝냈다. 122배 격차의 정체가 '엔진 성능' 이 아니라 **인덱스 부재**였다.
        """
        await self._run(
            "CREATE CONSTRAINT xgen_resource_uri IF NOT EXISTS "
            "FOR (n:Resource) REQUIRE n.uri IS UNIQUE"
        )
        return True

    async def _ensure_schema_once(self) -> None:
        if getattr(self, "_schema_ready", False):
            return
        try:
            await self.ensure_schema()
        except Exception:
            pass  # 권한 부족 등 — 적재 자체는 계속(느릴 뿐)
        self._schema_ready = True

    async def _run(self, cypher: str, **params):
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, **params)
            return [record async for record in result]

    # ── health ──
    async def health_check(self) -> bool:
        try:
            rows = await self._run("RETURN 1 AS ok")
            return bool(rows and rows[0]["ok"] == 1)
        except Exception:
            return False

    # ── B4: WRITE (같은 시그니처: triple_lines 문자열, bool 반환) ──
    async def insert_data(self, graph_name: str, triple_lines: str) -> bool:
        await self._ensure_schema_once()   # uri 인덱스 — 없으면 MERGE 가 풀스캔(122배 느림)
        rows = _parse_triples(triple_lines)
        if rows:
            await self._run(
                "UNWIND $rows AS row "
                "MERGE (a:Resource {uri: row.s}) "
                "MERGE (b:Resource {uri: row.o}) "
                "MERGE (a)-[:REL {p: row.p, g: $g}]->(b)",
                rows=rows, g=graph_name,
            )
        # 리터럴은 엣지가 아니라 노드 property (rdfs:label → n.label, sourceChunk → n.sourceChunk[]).
        # 동적 키는 Cypher 파라미터로 못 주므로 키별로 배치를 나눠 실행(키는 _SAFE_KEY_RE 로 통제).
        lits = _parse_literals(triple_lines)
        if lits:
            for key, batch in _group_literals_by_key(lits).items():
                # 모든 리터럴을 중복 없이 리스트로 누적(RDF 다중값 보존 — 덮어쓰기 금지).
                setter = (
                    f"SET n.`{key}` = CASE WHEN n.`{key}` IS NULL THEN [r.v] "
                    f"WHEN r.v IN n.`{key}` THEN n.`{key}` ELSE n.`{key}` + r.v END"
                )
                await self._run(
                    f"UNWIND $rows AS r MERGE (n:Resource {{uri: r.s}}) {setter}",
                    rows=batch,
                )
        return True

    async def delete_data(self, graph_name: str, triple_lines: str) -> bool:
        rows = _parse_triples(triple_lines)
        if not rows:
            return True
        await self._run(
            "UNWIND $rows AS row "
            "MATCH (a:Resource {uri: row.s})-[r:REL {p: row.p, g: $g}]->(b:Resource {uri: row.o}) "
            "DELETE r",
            rows=rows, g=graph_name,
        )
        return True

    async def triple_exists(self, graph_name: str, s: str, p: str, o: str) -> bool:
        rows = await self._run(
            "MATCH (a:Resource {uri: $s})-[r:REL {p: $p, g: $g}]->(b:Resource {uri: $o}) "
            "RETURN count(r) AS c",
            s=s, p=p, o=o, g=graph_name,
        )
        return bool(rows and rows[0]["c"] > 0)

    async def count_node_triples(self, graph_name: str, node_uri: str) -> int:
        # 트리플에서 노드가 주어인 수 + 목적어인 수 (RDF count_node_triples 등가).
        out_rows = await self._run(
            "MATCH (n:Resource {uri: $uri})-[r:REL {g: $g}]->() RETURN count(r) AS c",
            uri=node_uri, g=graph_name,
        )
        in_rows = await self._run(
            "MATCH ()-[r:REL {g: $g}]->(n:Resource {uri: $uri}) RETURN count(r) AS c",
            uri=node_uri, g=graph_name,
        )
        out = out_rows[0]["c"] if out_rows else 0
        inc = in_rows[0]["c"] if in_rows else 0
        return int(out + inc)

    # ── B5: 병합(2면 triple 이동) — 같은 시그니처 ──
    async def merge_move_subject(self, graph_name: str, uri: str, canonical: str) -> bool:
        """uri 가 주어인 엣지를 canonical 로 이동(LPG=엣지 재작성; RDF 2면이동의 주어측)."""
        await self._run(
            "MATCH (a:Resource {uri: $uri})-[r:REL {g: $g}]->(b) "
            "WITH r, b, r.p AS p "
            "MERGE (c:Resource {uri: $canonical}) "
            "MERGE (c)-[:REL {p: p, g: $g}]->(b) "
            "DELETE r",
            uri=uri, canonical=canonical, g=graph_name,
        )
        return True

    async def merge_move_object(self, graph_name: str, uri: str, canonical: str) -> bool:
        """uri 가 목적어인 엣지를 canonical 로 이동(RDF 2면이동의 목적어측)."""
        await self._run(
            "MATCH (a)-[r:REL {g: $g}]->(b:Resource {uri: $uri}) "
            "WITH r, a, r.p AS p "
            "MERGE (c:Resource {uri: $canonical}) "
            "MERGE (a)-[:REL {p: p, g: $g}]->(c) "
            "DELETE r",
            uri=uri, canonical=canonical, g=graph_name,
        )
        return True

    # ── 온톨로지 빌드 (kg_builder.build_and_upload 경로) ──
    #
    # 실제 적재는 insert_data 가 아니라 **upload_ttl** 이다(kg_builder.py:602/610).
    # 빌드 호출 순서: ensure_dataset → clear_graph → upload_ttl → (정제 2종)
    # 이후 파이프라인이 get_triple_count 로 검증한다.

    async def ensure_dataset(self) -> bool:
        """Fuseki 는 데이터셋을 만들지만 LPG 는 DB 가 이미 있다 — 스키마/인덱스 보장으로 대응."""
        await self.ensure_schema()
        return True

    async def clear_graph(self, graph_name: Optional[str] = None) -> bool:
        """named graph 비우기. RDF `CLEAR SILENT GRAPH` 등가 — 없어도 실패하지 않는다.

        LPG 에는 named graph 가 없으므로 엣지의 g 속성으로 범위를 잡는다.
        고립된 Resource 노드(엣지가 모두 사라진 것)도 함께 정리해 RDF 의미에 맞춘다.
        """
        if graph_name:
            await self._run("MATCH ()-[r:REL {g: $g}]->() DELETE r", g=graph_name)
        else:
            await self._run("MATCH ()-[r:REL]->() DELETE r")
        # 엣지가 사라져 고아가 된 노드 정리(라벨/청크만 남은 잔재 제거).
        await self._run(
            "MATCH (n:Resource) WHERE NOT (n)-[:REL]-() DETACH DELETE n"
        )
        return True

    async def upload_ttl(self, ttl_content: str, graph_name: Optional[str] = None):
        """**빌드의 핵심 적재 경로.** Turtle 문자열을 받아 LPG 로 적재.

        kg_builder 는 rdflib 로 만든 그래프를 `serialize(format="turtle")` 로 넘긴다.
        여기서는 rdflib 로 파싱해 N-Triples 로 정규화한 뒤 기존 insert_data 경로를 재사용한다
        (파싱 규칙을 두 벌 두면 같은 입력이 백엔드별로 다른 그래프가 된다).

        반환형은 Fuseki 와 동일하게 dict(성공 여부 포함).
        """
        g = graph_name or ""
        try:
            from rdflib import Graph as _RDFGraph
        except ImportError as e:  # 의존성 부재를 조용히 넘기지 않는다
            raise RuntimeError(
                "upload_ttl 은 Turtle 파싱에 rdflib 가 필요하다 — `pip install rdflib`"
            ) from e

        rg = _RDFGraph()
        rg.parse(data=ttl_content, format="turtle")
        nt = rg.serialize(format="nt")
        await self.insert_data(g, nt)
        return {"success": True, "triples": len(rg), "graph": g}

    async def get_triple_count(self, graph_name: Optional[str] = None) -> int:
        """트리플 수(엣지 + 해당 그래프 노드의 리터럴 값).

        ⚠️ **구조적 한계**: RDF 는 리터럴 트리플도 named graph 에 속하지만, LPG 에서 리터럴은
        노드 property 라 **그래프 소속 정보가 없다**. 그래서 리터럴은 "그 그래프의 엣지에
        참여한 노드" 로 범위를 좁혀 센다 — 여러 그래프가 같은 노드를 공유하면 중복 계상된다.
        (초기 구현은 범위를 아예 안 잡아 전체 노드를 훑었고 1,202 → 15,004 으로 12배 과다였다.)

        빌드 파이프라인은 이 값을 **적재 검증용 근사치**로 쓴다(pipeline.py 의 reconcile).
        정확한 RDF 등가가 필요하면 리터럴에도 그래프 태깅이 필요하다 — DEBTS §B(named graph).
        """
        if graph_name:
            rows = await self._run(
                "MATCH ()-[r:REL {g: $g}]->() RETURN count(r) AS c", g=graph_name)
            lit_rows = await self._run(
                "MATCH (n:Resource)-[:REL {g: $g}]-() WITH DISTINCT n "
                "WITH n, [k IN keys(n) WHERE k <> 'uri'] AS ks "
                "UNWIND ks AS k WITH n[k] AS v "
                "RETURN sum(CASE WHEN v IS :: LIST<ANY> THEN size(v) ELSE 1 END) AS c",
                g=graph_name)
        else:
            rows = await self._run("MATCH ()-[r:REL]->() RETURN count(r) AS c")
            lit_rows = await self._run(
                "MATCH (n:Resource) WITH n, [k IN keys(n) WHERE k <> 'uri'] AS ks "
                "UNWIND ks AS k WITH n[k] AS v "
                "RETURN sum(CASE WHEN v IS :: LIST<ANY> THEN size(v) ELSE 1 END) AS c")
        edges = int(rows[0]["c"]) if rows else 0
        lits = int(lit_rows[0]["c"] or 0) if lit_rows and lit_rows[0]["c"] is not None else 0
        return edges + lits

    async def clean_subclassof_noise(self, graph_name: Optional[str] = None) -> bool:
        """subClassOf 정제 — 부모가 클래스가 아니라 **속성(관계)** 인 잘못된 is-a 엣지 제거.

        원본 SPARQL: DELETE { ?c rdfs:subClassOf ?p } WHERE { ?c rdfs:subClassOf ?p .
                     ?p a ?t . FILTER(?t IN (owl:ObjectProperty, owl:DatatypeProperty)) }
        "이어져있다고 다 상속 아니다" — 추출기가 관계명을 부모로 잘못 박은 노이즈를 차단한다.
        """
        gcond = "{g: $g}" if graph_name else ""
        await self._run(
            f"MATCH (c:Resource)-[r:REL {{p: $sub{', g: $g' if graph_name else ''}}}]->(p:Resource) "
            f"MATCH (p)-[:REL {{p: $type}}]->(t:Resource) "
            "WHERE t.uri IN $prop_types "
            "DELETE r",
            sub=_RDFS_SUBCLASS, type=_RDF_TYPE, prop_types=_PROPERTY_TYPES,
            **({"g": graph_name} if graph_name else {}),
        )
        return True

    async def materialize_property_inheritance(self, graph_name: Optional[str] = None) -> bool:
        """직계(1-level) 상속 구체화 — 부모 클래스의 domain 속성을 자식에게 전파.

        원본과 동일한 **유효 is-a 게이트**: 부모가 진짜 owl:Class 여야 하고,
        속성으로도 타입된 부모는 제외(관계명이 부모로 잘못 박힌 노이즈 차단).
        transitive 가 아니라 직계만 — 밀집 계층에서 폭증하기 때문.
        """
        gp = {"g": graph_name} if graph_name else {}
        gc = ", g: $g" if graph_name else ""
        await self._run(
            f"MATCH (child:Resource)-[:REL {{p: $sub{gc}}}]->(parent:Resource) "
            f"MATCH (child)-[:REL {{p: $type{gc}}}]->(:Resource {{uri: $owl_class}}) "
            f"MATCH (parent)-[:REL {{p: $type{gc}}}]->(:Resource {{uri: $owl_class}}) "
            f"WHERE NOT EXISTS {{ MATCH (parent)-[:REL {{p: $type{gc}}}]->(pt:Resource) "
            "  WHERE pt.uri IN $prop_types } "
            f"MATCH (prop:Resource)-[:REL {{p: $domain{gc}}}]->(parent) "
            f"MATCH (prop)-[:REL {{p: $type{gc}}}]->(ptt:Resource) WHERE ptt.uri IN $prop_types "
            f"AND NOT EXISTS {{ MATCH (prop)-[:REL {{p: $domain{gc}}}]->(child) }} "
            f"MERGE (prop)-[:REL {{p: $domain{gc}}}]->(child)",
            sub=_RDFS_SUBCLASS, type=_RDF_TYPE, domain=_RDFS_DOMAIN,
            owl_class=_OWL_CLASS, prop_types=_PROPERTY_TYPES, **gp,
        )
        return True

    # ── 그래프 검색 (DEBTS §A 착수순서 1·2 — fulltext 무관 부분부터) ──
    #
    # ⚠️ 반환형은 **Fuseki 와 동일한 SPARQL JSON bindings** 여야 한다.
    # 호출부(documents multi_turn_rag)가 res["results"]["bindings"] 를 직접 파싱하고
    # 변수명(sLabel/pLabel/oLabel, p/pl)까지 그대로 읽기 때문. 여기서 형태가 어긋나면
    # 또 조용한 빈 결과가 된다(§15.4 무증상 실패와 같은 계통).

    @staticmethod
    def _bindings(rows: List[dict]) -> dict:
        """Cypher 결과 → SPARQL JSON 형태. None 값 키는 생략(SPARQL OPTIONAL 미바인딩 등가)."""
        out = []
        for r in rows:
            b = {}
            for k, v in r.items():
                if v is None:
                    continue
                b[k] = {"type": "literal", "value": str(v)}
            out.append(b)
        return {"results": {"bindings": out}}

    @staticmethod
    def _parse_values(values: str) -> List[str]:
        """호출부가 조립한 SPARQL VALUES 리터럴 목록(`"a" "b"`) → 파이썬 리스트."""
        return [_unescape(v) for v in re.findall(r'"((?:[^"\\]|\\.)*)"', values or "")]

    # ── fulltext 검색 (DEBTS §A 4단계) ──
    #
    # Fuseki 는 jena-text(Lucene, CJK bigram)로 rdfs:label 을 색인하고
    #   `(?s ?score) text:query (rdfs:label "terms" N)` 으로 **상위 N개**를 받는다.
    # ⚠️ N(15/80/60/30)은 Lucene '점수 임계' 가 아니라 **개수 제한**이다 — 등가 이식이 가능하다.
    # Neo4j 는 같은 Lucene 엔진의 full-text index 를 쓰고 analyzer 를 cjk 로 맞춘다.

    FULLTEXT_INDEX = "xgen_resource_label"

    async def ensure_fulltext_index(self) -> bool:
        """label 전문검색 인덱스 생성(멱등). analyzer=cjk 로 Fuseki 의 CJKAnalyzer 와 정렬."""
        await self._run(
            f"CREATE FULLTEXT INDEX {self.FULLTEXT_INDEX} IF NOT EXISTS "
            "FOR (n:Resource) ON EACH [n.label] "
            "OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}"
        )
        # 인덱스가 온라인이 될 때까지 대기 — 직후 질의하면 빈 결과가 나올 수 있다(무증상 위험).
        await self._run(f"CALL db.awaitIndex('{self.FULLTEXT_INDEX}', 300)")
        return True

    async def _ft_nodes(self, terms: str, top_n: int) -> List[str]:
        """text:query 등가 — label 전문검색 상위 N개 노드 uri. Lucene 특수문자는 이스케이프."""
        if not terms or not terms.strip():
            return []
        q = _lucene_escape(terms)
        rows = await self._run(
            f"CALL db.index.fulltext.queryNodes('{self.FULLTEXT_INDEX}', $q) "
            "YIELD node, score RETURN node.uri AS uri ORDER BY score DESC LIMIT $n",
            q=q, n=int(top_n),
        )
        return [r["uri"] for r in rows if r.get("uri")]

    async def seed_relations_by_fulltext_forward(self, graph_name: str, terms: str, pin: str):
        """정방향 정밀관계: label 검색 상위15 를 주어로, 술어라벨이 pin 목록에 든 관계만.

        원본은 목적어 라벨이 없으면 URI 를 쓴다(COALESCE(STR(?ol2), STR(?o))) — 그대로 재현.
        """
        seeds = await self._ft_nodes(terms, 15)
        pins = _parse_pin(pin)
        if not seeds or not pins:
            return self._bindings([])
        rows = await self._run(
            "MATCH (s:Resource)-[r:REL {g: $g}]->(o:Resource) "
            "WHERE s.uri IN $seeds AND s.label IS NOT NULL "
            "MATCH (p:Resource {uri: r.p}) WHERE p.label IS NOT NULL "
            "WITH s, o, p WHERE any(x IN p.label WHERE x IN $pins) "
            "UNWIND s.label AS sl UNWIND p.label AS pl "
            "WITH sl, pl, o WHERE pl IN $pins "
            "WITH sl, pl, coalesce(o.label, [o.uri]) AS ols "
            "UNWIND ols AS ol RETURN DISTINCT sl, pl, ol LIMIT 40",
            g=graph_name, seeds=seeds, pins=pins,
        )
        return self._bindings(rows)

    async def seed_relations_by_fulltext_reverse(self, graph_name: str, terms: str, pin: str):
        """역방향: label 검색 상위15 를 **목적어**로. 원본은 주어·목적어 라벨 모두 필수."""
        seeds = await self._ft_nodes(terms, 15)
        pins = _parse_pin(pin)
        if not seeds or not pins:
            return self._bindings([])
        rows = await self._run(
            "MATCH (s:Resource)-[r:REL {g: $g}]->(o:Resource) "
            "WHERE o.uri IN $seeds AND o.label IS NOT NULL AND s.label IS NOT NULL "
            "MATCH (p:Resource {uri: r.p}) WHERE p.label IS NOT NULL "
            "UNWIND s.label AS sl UNWIND p.label AS pl UNWIND o.label AS ol "
            "WITH sl, pl, ol WHERE pl IN $pins "
            "RETURN DISTINCT sl, pl, ol LIMIT 40",
            g=graph_name, seeds=seeds, pins=pins,
        )
        return self._bindings(rows)

    async def seed_connectivity_relations(self, graph_name: str, terms: str, limit: int):
        """연결성 시드: **주어·목적어 양끝 모두** label 검색 상위80 에 들어야 한다(가장 좁은 시드)."""
        seeds = await self._ft_nodes(terms, 80)
        if not seeds:
            return self._bindings([])
        rows = await self._run(
            "MATCH (s:Resource)-[r:REL {g: $g}]->(o:Resource) "
            "WHERE s.uri IN $seeds AND o.uri IN $seeds AND s.uri <> o.uri "
            "  AND s.label IS NOT NULL AND o.label IS NOT NULL "
            "  AND NOT r.p IN $excl "
            "OPTIONAL MATCH (p:Resource {uri: r.p}) "
            "WITH s, o, coalesce(p.label, [null]) AS pls "
            "UNWIND s.label AS sLabel UNWIND pls AS pLabel UNWIND o.label AS oLabel "
            "RETURN DISTINCT sLabel, pLabel, oLabel LIMIT $lim",
            g=graph_name, seeds=seeds, excl=_EXCLUDED_PREDS, lim=int(limit),
        )
        return self._bindings(rows)

    async def seed_relations_broad(self, graph_name: str, terms: str, limit: int):
        """recall 폴백: **주어만** label 검색 상위60 에 들면 된다(연결성 시드가 비었을 때의 유일 경로)."""
        seeds = await self._ft_nodes(terms, 60)
        if not seeds:
            return self._bindings([])
        rows = await self._run(
            "MATCH (s:Resource)-[r:REL {g: $g}]->(o:Resource) "
            "WHERE s.uri IN $seeds AND s.label IS NOT NULL AND o.label IS NOT NULL "
            "  AND NOT r.p IN $excl "
            "OPTIONAL MATCH (p:Resource {uri: r.p}) "
            "WITH s, o, coalesce(p.label, [null]) AS pls "
            "UNWIND s.label AS sLabel UNWIND pls AS pLabel UNWIND o.label AS oLabel "
            "RETURN DISTINCT sLabel, pLabel, oLabel LIMIT $lim",
            g=graph_name, seeds=seeds, excl=_EXCLUDED_PREDS, lim=int(limit),
        )
        return self._bindings(rows)

    async def seed_classes_by_fulltext(self, graph_name: str, terms: str,
                                       mode: str = "closure"):
        """클래스 전수 시드: label 검색 상위30 중 owl:Class 인 것 + 인스턴스 집계.

        원본 반환: ?cl(클래스라벨) ?n(인스턴스수) ?insts(' | ' 구분 인스턴스라벨), 상위 3개.
        GROUP_CONCAT → Cypher collect + reduce 로 등가 구현.

        mode 는 Fuseki 와 같은 이름·기본값이다("closure"/"direct").
        `queries.seed_classes_by_fulltext_query` 의 폐포 2종을 그대로 옮긴다:
        - 동치 폐포 `?c (owl:equivalentClass|^owl:equivalentClass)* ?ceq` → 무방향 `*0..`
        - 이행 폐포 `?sub rdfs:subClassOf* ?ceq` (closure 에서만) → 방향 `*0..`
        `*0..` 는 zero-length 포함이라 링크가 없으면 무변경(SPARQL `*` 와 동일).
        """
        seeds = await self._ft_nodes(terms, 30)
        if not seeds:
            return self._bindings([])
        # 폐포 대상 클래스 집합. direct 는 동치까지만, closure 는 하위클래스까지.
        expand = ("MATCH (sub:Resource)-[:REL*0.. {p: $sub_p, g: $g}]->(ceq) "
                  if mode != "direct" else "WITH c, ceq AS sub ")
        rows = await self._run(
            "MATCH (c:Resource)-[:REL {p: $type_p, g: $g}]->(:Resource {uri: $owl_class}) "
            "WHERE c.uri IN $seeds AND c.label IS NOT NULL "
            "MATCH (c)-[:REL*0.. {p: $eq_p, g: $g}]-(ceq:Resource) "
            + expand +
            "MATCH (i:Resource)-[:REL {p: $type_p, g: $g}]->(sub) WHERE i.label IS NOT NULL "
            "WITH c, count(DISTINCT i) AS n, collect(DISTINCT i.label[0]) AS ils "
            "RETURN c.label[0] AS cl, n, ils ORDER BY n DESC LIMIT 3",
            seeds=seeds, type_p=_RDF_TYPE, owl_class=_OWL_CLASS,
            eq_p=_OWL_EQ_CLASS, sub_p=_RDFS_SUBCLASS, g=graph_name,
        )
        out = [{"cl": r["cl"], "n": r["n"], "insts": " | ".join(x for x in (r["ils"] or []) if x)}
               for r in rows]
        return self._bindings(out)

    async def seed_chunk_relations(self, graph_name: str, values: str, limit: int):
        """청크 1홉 관계 시드 (HippoRAG 확장). fulltext 무관 — VALUES 바인딩이라 Cypher 직역 가능.

        원본 SPARQL: ?s :sourceChunk ?c(VALUES) . ?s rdfs:label ?sLabel . ?s ?p ?o .
                     FILTER(?p 가 type/label/sourceChunk/sourceDocument/scsContextSummary 아님)
                     ?o rdfs:label ?oLabel . OPTIONAL { ?p rdfs:label ?pLabel }
        LPG: sourceChunk 는 노드 property(리스트), 관계는 REL 엣지, 술어라벨은 localname.
        """
        chunks = self._parse_values(values)
        if not chunks:
            return self._bindings([])
        # 술어 라벨은 술어 URI 자신의 rdfs:label 트리플에서 온다(원본 SPARQL 의 OPTIONAL).
        # 실데이터에서 label 값은 localname 과 다르다(예: org_alternate_names → "org:alternate_names",
        # coOccursWith → "co-occurs with"). localname 으로 대체하면 조용히 다른 값이 나간다.
        # 술어 URI 도 Resource 노드로 적재되므로 그 노드의 label 을 조인해 읽는다.
        # label 은 리스트(RDF 다중값). SPARQL 은 값마다 해(solution)를 내므로 UNWIND 로 펼친다.
        # pLabel 은 OPTIONAL — 없으면 null 한 행(원본 SPARQL 의 OPTIONAL 미바인딩과 등가).
        rows = await self._run(
            "MATCH (s:Resource)-[r:REL {g: $g}]->(o:Resource) "
            "WHERE s.sourceChunk IS NOT NULL AND any(c IN s.sourceChunk WHERE c IN $chunks) "
            "  AND s.label IS NOT NULL AND o.label IS NOT NULL "
            # 0824: 정본이 정밀 SVO 와 동시출현 약관계를 슬롯 분리한다. 이 슬롯에서
            # coOccursWith 를 빼지 않으면 coarse 엣지가 LIMIT 를 선점한다.
            "  AND r.p <> $cooc "
            "OPTIONAL MATCH (p:Resource {uri: r.p}) "
            "WITH s, o, coalesce(p.label, [null]) AS pls "
            "UNWIND s.label AS sLabel UNWIND pls AS pLabel UNWIND o.label AS oLabel "
            "RETURN DISTINCT sLabel, pLabel, oLabel "
            "LIMIT $limit",
            g=graph_name, chunks=chunks, limit=int(limit), cooc=_COOC_URI,
        )
        return self._bindings(rows)

    async def seed_chunk_cooccurrence(self, graph_name: str, values: str, limit: int):
        """동시출현 약관계 슬롯 (0824). fulltext 무관 — VALUES 바인딩이라 Cypher 직역.

        원본 SPARQL(`cq`): 같은 base 에 `FILTER(?p = :coOccursWith)` 를 걸고
        **?pLabel 을 조회하지 않는다** — 술어가 단일이라 ko/en 라벨 2행 중복으로 슬롯
        절반이 낭비되는 것을 막기 위함. 표기는 호출부가 "함께언급" 으로 고정한다.
        """
        chunks = self._parse_values(values)
        if not chunks:
            return self._bindings([])
        rows = await self._run(
            "MATCH (s:Resource)-[r:REL {g: $g}]->(o:Resource) "
            "WHERE s.sourceChunk IS NOT NULL AND any(c IN s.sourceChunk WHERE c IN $chunks) "
            "  AND s.label IS NOT NULL AND o.label IS NOT NULL "
            "  AND r.p = $cooc "
            "UNWIND s.label AS sLabel UNWIND o.label AS oLabel "
            "RETURN DISTINCT sLabel, oLabel "
            "LIMIT $limit",
            g=graph_name, chunks=chunks, limit=int(limit), cooc=_COOC_URI,
        )
        return self._bindings(rows)

    async def predicate_labels(self, graph_name: str):
        """그래프에서 실제 쓰인 술어 + 라벨. 순수 SPARQL 이라 Cypher 직역 가능(fulltext 무관).

        원본 반환 변수: ?p (술어 URI) / ?pl (술어 라벨).
        """
        # pl 은 술어 URI 자신의 rdfs:label(= Resource 노드의 label property). localname 이 아니다.
        # ⚠️ 원본 SPARQL 은 `?s ?p ?o . ?p rdfs:label ?pl` — **필수 패턴**이라 label 없는 술어는
        # 결과에서 아예 빠진다(OPTIONAL 아님). 여기서 OPTIONAL 로 풀면 null 행이 섞여 계약이 어긋난다.
        rows = await self._run(
            "MATCH ()-[r:REL {g: $g}]->() WITH DISTINCT r.p AS p "
            "MATCH (n:Resource {uri: p}) WHERE n.label IS NOT NULL "
            "UNWIND n.label AS pl RETURN DISTINCT p, pl",
            g=graph_name,
        )
        return self._bindings(rows)

    # ── GDS 반복형 그래프알고리즘 (§13 실측 채택 — ArcadeDB 미지원, Neo4j 선택 사유) ──
    async def _gds_project(self, graph_name: str, proj: str) -> None:
        """named-graph g 의 REL 을 UNDIRECTED 로 투영. (§13.2: DIRECTED 는 커뮤니티 과분할 함정)"""
        await self._run(f"CALL gds.graph.drop('{proj}', false) YIELD graphName")
        await self._run(
            "MATCH (a:Resource)-[r:REL {g: $g}]->(b:Resource) "
            f"WITH gds.graph.project('{proj}', a, b, {{}}, {{undirectedRelationshipTypes: ['*']}}) AS gr "
            "RETURN gr.graphName AS name",
            g=graph_name,
        )

    async def community_detect(self, graph_name: str) -> List[dict]:
        """Louvain 커뮤니티 탐지. 반환: [{uri, community}] — 노드별 커뮤니티 id.

        엔진 in-DB 실행(데이터 미이동). §13.3 실측: 수만 노드 이상서 앱측 pull+계산 대비 우세.
        """
        proj = f"cd_{abs(hash(graph_name)) % 10_000_000}"
        try:
            await self._gds_project(graph_name, proj)
            rows = await self._run(
                f"CALL gds.louvain.stream('{proj}') YIELD nodeId, communityId "
                "RETURN gds.util.asNode(nodeId).uri AS uri, communityId AS community",
            )
            return [{"uri": r["uri"], "community": int(r["community"])} for r in rows]
        finally:
            await self._run(f"CALL gds.graph.drop('{proj}', false) YIELD graphName")

    async def pagerank(self, graph_name: str, top: int = 0) -> List[dict]:
        """PageRank 중심성. 반환: [{uri, score}] score 내림차순. top>0 이면 상위 top 개만."""
        proj = f"pr_{abs(hash(graph_name)) % 10_000_000}"
        limit = f" LIMIT {int(top)}" if top and top > 0 else ""
        try:
            await self._gds_project(graph_name, proj)
            rows = await self._run(
                f"CALL gds.pageRank.stream('{proj}') YIELD nodeId, score "
                "RETURN gds.util.asNode(nodeId).uri AS uri, score "
                f"ORDER BY score DESC{limit}",
            )
            return [{"uri": r["uri"], "score": float(r["score"])} for r in rows]
        finally:
            await self._run(f"CALL gds.graph.drop('{proj}', false) YIELD graphName")

    def __getattr__(self, name: str):
        # dunder/private 접근(introspection·pickle·hasattr·pytest 내부)은 정상 AttributeError.
        if name.startswith("_"):
            raise AttributeError(name)
        from xgen_graphstore.errors import CapabilityError

        # 근본 능력 공백(fulltext/named-graph/owl/ttl/raw)=CapabilityError(라우팅 신호).
        cap = METHOD_CAPABILITY.get(name)
        if cap is not None:
            raise CapabilityError(
                f"backend 'neo4j' 는 '{cap.value}' 능력 미지원 (메서드 '{name}') — "
                f"DEBTS.md {cap.value} 항목(3층 LPG 재모델링) 대상."
            )
        # 그 외(구현 가능하나 PoC 미완, 예: rename_*)=NotImplementedError.
        from xgen_graphstore.capabilities import implemented_methods

        raise NotImplementedError(
            f"Neo4jBackend 는 '{name}' 미구현 — 능력 공백이 아니라 구현 가능·미완이다. "
            f"현재 구현: {', '.join(implemented_methods(self))}"
        )
