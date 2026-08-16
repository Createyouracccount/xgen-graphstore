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
from typing import List, Optional, Tuple

# `<s> <p> <o> .` (N-Triples, 리소스만) 파서 — FusekiBackend 가 받는 triple_lines 형식과 동일.
_TRIPLE_RE = re.compile(r"<([^>]+)>\s+<([^>]+)>\s+<([^>]+)>\s*\.")


def _parse_triples(triple_lines: str) -> List[dict]:
    return [
        {"s": s, "p": p, "o": o}
        for (s, p, o) in _TRIPLE_RE.findall(triple_lines)
    ]


class Neo4jBackend:
    """OntologyStore 계약의 LPG 구현(PoC). FusekiBackend 와 동일 시그니처."""

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
        if not rows:
            return True
        await self._run(
            "UNWIND $rows AS row "
            "MERGE (a:Resource {uri: row.s}) "
            "MERGE (b:Resource {uri: row.o}) "
            "MERGE (a)-[:REL {p: row.p, g: $g}]->(b)",
            rows=rows, g=graph_name,
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

    def __getattr__(self, name: str):
        # 미구현 계약 메서드는 조용한 실패 대신 명시적 NotImplementedError.
        raise NotImplementedError(
            f"Neo4jBackend(PoC)는 '{name}' 미구현 — DEBTS.md 3층 H 항목(LPG 재모델링) 대상. "
            f"현재 PoC 스코프: insert_data/delete_data/triple_exists/count_node_triples/"
            f"merge_move_subject/merge_move_object/health_check."
        )
