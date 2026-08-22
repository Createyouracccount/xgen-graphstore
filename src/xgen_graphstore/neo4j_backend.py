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


class Neo4jBackend:
    """OntologyStore 계약의 LPG 구현(PoC). FusekiBackend 와 동일 시그니처."""

    BACKEND_NAME = "neo4j"
    # 리소스-트리플 CRUD + GDS 반복형 그래프알고리즘(커뮤니티탐지/PageRank, §13 실측 채택).
    # fulltext/named-graph/owl/ttl/raw 는 미보유(DEBTS H).
    CAPABILITIES = frozenset({Capability.CORE_TRIPLE_RW, Capability.GRAPH_ALGORITHMS})

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
            "OPTIONAL MATCH (p:Resource {uri: r.p}) "
            "WITH s, o, coalesce(p.label, [null]) AS pls "
            "UNWIND s.label AS sLabel UNWIND pls AS pLabel UNWIND o.label AS oLabel "
            "RETURN DISTINCT sLabel, pLabel, oLabel "
            "LIMIT $limit",
            g=graph_name, chunks=chunks, limit=int(limit),
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
        raise NotImplementedError(
            f"Neo4jBackend(PoC)는 '{name}' 미구현 — 구현 가능하나 PoC 스코프 밖. "
            f"현재: insert_data/delete_data/triple_exists/count_node_triples/"
            f"merge_move_subject/merge_move_object/health_check."
        )
