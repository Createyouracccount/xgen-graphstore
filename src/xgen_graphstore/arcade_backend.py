"""ArcadeBackend — ArcadeDB(네이티브 다중모델 LPG) 백엔드 PoC. Cypher via HTTP API.

선정 근거: Apache-2.0(영구서약) + ~70 네이티브 그래프 알고리즘(Louvain/Leiden/PageRank/shortestPath)
+ LSM-tree 경량 인덱스 + Cypher/Gremlin. Apache AGE(알고리즘 부재·GIN 인덱스 과중)의 대체.
정본: company/xgen-levelup/docs/그래프DB_선정_2026_08_17.md §10.

FusekiBackend/Neo4jBackend 와 **동일 OntologyStore 계약**. create_store({"backend":"arcade"}) 로 스왑.
PoC 스코프 = 리소스-트리플 CRUD 6메서드 + health + shortest_path(알고리즘 실증, Fuseki가 못 하던 것).

RDF triple (s,p,o) @ graph g  →  (:Resource {uri:s})-[:REL {p:p, g:g}]->(:Resource {uri:o})
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

import httpx

from xgen_graphstore.capabilities import Capability, METHOD_CAPABILITY

_TRIPLE_RE = re.compile(r"<([^>]+)>\s+<([^>]+)>\s+<([^>]+)>\s*\.")


def _parse_triples(triple_lines: str):
    return _TRIPLE_RE.findall(triple_lines)  # [(s,p,o), ...]


def _q(s: str) -> str:
    """Cypher 이중따옴표 리터럴 이스케이프(PoC — 파라미터 대신)."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


class ArcadeBackend:
    """OntologyStore 계약의 ArcadeDB 구현(PoC). HTTP command API(Cypher)."""

    BACKEND_NAME = "arcade"
    # PoC 스코프 = 리소스-트리플 CRUD. fulltext/named-graph/owl/ttl/raw 는 미보유(추후).
    CAPABILITIES = frozenset({Capability.CORE_TRIPLE_RW})

    def __init__(
        self,
        base_url: Optional[str] = None,
        database: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self._base = (base_url or os.getenv("ARCADE_URL", "http://localhost:2480")).rstrip("/")
        self._db = database or os.getenv("ARCADE_DB", "xgen")
        self._user = username or os.getenv("ARCADE_USER", "root")
        self._pw = password or os.getenv("ARCADE_PASSWORD", "")
        self._schema_ready = False

    async def _cmd(self, command: str, language: str = "cypher"):
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{self._base}/api/v1/command/{self._db}",
                json={"language": language, "command": command},
                auth=(self._user, self._pw),
            )
            r.raise_for_status()
            return r.json().get("result", [])

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        for sql in (
            "CREATE VERTEX TYPE Resource IF NOT EXISTS",
            "CREATE PROPERTY Resource.uri STRING",
            "CREATE INDEX IF NOT EXISTS ON Resource (uri) UNIQUE",
            "CREATE EDGE TYPE REL IF NOT EXISTS",
        ):
            try:
                await self._cmd(sql, language="sql")
            except Exception:
                pass  # 이미 존재 등 — 무해
        self._schema_ready = True

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self._base}/api/v1/databases", auth=(self._user, self._pw))
                return r.status_code == 200
        except Exception:
            return False

    # ── CORE: WRITE (같은 시그니처: triple_lines 문자열, bool 반환) ──
    async def insert_data(self, graph_name: str, triple_lines: str) -> bool:
        await self._ensure_schema()
        g = _q(graph_name)
        triples = _parse_triples(triple_lines)
        if not triples:
            return True
        # 배치: UNWIND 인라인 리스트로 한 번에(트리플별 왕복 회피).
        rows = ",".join(
            f'{{s:"{_q(s)}",o:"{_q(o)}",p:"{_q(p)}"}}' for s, p, o in triples
        )
        await self._cmd(
            f'UNWIND [{rows}] AS row '
            f'MERGE (a:Resource {{uri:row.s}}) '
            f'MERGE (b:Resource {{uri:row.o}}) '
            f'MERGE (a)-[:REL {{p:row.p, g:"{g}"}}]->(b)'
        )
        return True

    async def delete_data(self, graph_name: str, triple_lines: str) -> bool:
        g = _q(graph_name)
        for s, p, o in _parse_triples(triple_lines):
            s, p, o = _q(s), _q(p), _q(o)
            await self._cmd(
                f'MATCH (a:Resource {{uri:"{s}"}})-[r:REL {{p:"{p}", g:"{g}"}}]->(b:Resource {{uri:"{o}"}}) '
                f'DELETE r'
            )
        return True

    async def triple_exists(self, graph_name: str, s: str, p: str, o: str) -> bool:
        s, p, o, g = _q(s), _q(p), _q(o), _q(graph_name)
        res = await self._cmd(
            f'MATCH (a:Resource {{uri:"{s}"}})-[r:REL {{p:"{p}", g:"{g}"}}]->(b:Resource {{uri:"{o}"}}) '
            f'RETURN count(r) AS c'
        )
        return bool(res) and int(res[0].get("c", 0)) > 0

    async def count_node_triples(self, graph_name: str, node_uri: str) -> int:
        uri, g = _q(node_uri), _q(graph_name)
        out = await self._cmd(
            f'MATCH (n:Resource {{uri:"{uri}"}})-[r:REL {{g:"{g}"}}]->() RETURN count(r) AS c'
        )
        inc = await self._cmd(
            f'MATCH ()-[r:REL {{g:"{g}"}}]->(n:Resource {{uri:"{uri}"}}) RETURN count(r) AS c'
        )
        oc = int(out[0].get("c", 0)) if out else 0
        ic = int(inc[0].get("c", 0)) if inc else 0
        return oc + ic

    async def merge_move_subject(self, graph_name: str, uri: str, canonical: str) -> bool:
        u, c, g = _q(uri), _q(canonical), _q(graph_name)
        await self._cmd(
            f'MATCH (a:Resource {{uri:"{u}"}})-[r:REL {{g:"{g}"}}]->(b) '
            f'MERGE (cc:Resource {{uri:"{c}"}}) '
            f'MERGE (cc)-[:REL {{p:r.p, g:"{g}"}}]->(b) '
            f'DELETE r'
        )
        return True

    async def merge_move_object(self, graph_name: str, uri: str, canonical: str) -> bool:
        u, c, g = _q(uri), _q(canonical), _q(graph_name)
        await self._cmd(
            f'MATCH (a)-[r:REL {{g:"{g}"}}]->(b:Resource {{uri:"{u}"}}) '
            f'MERGE (cc:Resource {{uri:"{c}"}}) '
            f'MERGE (a)-[:REL {{p:r.p, g:"{g}"}}]->(cc) '
            f'DELETE r'
        )
        return True

    # ── ALGORITHM (AGE가 못하던 것 — 네이티브 최단경로) ──
    async def shortest_path(self, from_uri: str, to_uri: str) -> list:
        """SQL shortestPath — 네이티브 그래프 알고리즘. 노드 rid 경로 반환."""
        f, t = _q(from_uri), _q(to_uri)
        res = await self._cmd(
            f'SELECT shortestPath('
            f'(SELECT FROM Resource WHERE uri="{f}"), '
            f'(SELECT FROM Resource WHERE uri="{t}")) AS path',
            language="sql",
        )
        return res[0].get("path", []) if res else []

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        from xgen_graphstore.errors import CapabilityError

        cap = METHOD_CAPABILITY.get(name)
        if cap is not None:
            raise CapabilityError(
                f"backend 'arcade'(PoC)는 '{cap.value}' 능력 미구현 (메서드 '{name}')."
            )
        raise NotImplementedError(f"ArcadeBackend(PoC)는 '{name}' 미구현.")
