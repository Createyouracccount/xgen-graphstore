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

# N-Triples 파싱은 백엔드 공통(ntriples 모듈) — 백엔드마다 다르게 파싱하면 같은 입력이
# 백엔드별로 다른 그래프가 되어 스왑 계약이 깨진다.
from xgen_graphstore.ntriples import (  # noqa: E402
    group_literals_by_key as _group_literals_by_key,
    parse_literals as _parse_literals,
    parse_triples as _parse_triples,
    unescape as _unescape,
)


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
        if triples:
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
        # 리터럴 → 노드 property. 이게 없으면 검색이 성립하지 않는다(실측: label 제거 시 314건→0건).
        # 검색 쿼리 7종이 전부 rdfs:label 을 참조하고, fulltext 5종은 text:query 가 label 자체를
        # 검색 대상으로 삼는다. 예전엔 리터럴을 **조용히 버려** 입력의 45%가 사라졌다.
        lits = _parse_literals(triple_lines)
        for key, batch in _group_literals_by_key(lits).items():
            rows = ",".join(f'{{s:"{_q(r["s"])}",v:"{_q(r["v"])}"}}' for r in batch)
            # RDF 다중값 보존: 덮어쓰지 않고 중복 없이 누적(단일값 취급 시 조용한 소실).
            await self._cmd(
                f'UNWIND [{rows}] AS row '
                f'MERGE (n:Resource {{uri:row.s}}) '
                f'SET n.`{key}` = CASE WHEN n.`{key}` IS NULL THEN [row.v] '
                f'WHEN row.v IN n.`{key}` THEN n.`{key}` ELSE n.`{key}` + row.v END'
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

    # ── 그래프 검색 (DEBTS §A 착수순서 1·2 — fulltext 무관분) ──
    #
    # ⚠️ 반환형은 Fuseki 와 동일한 SPARQL JSON bindings 여야 한다(호출부가 그대로 파싱).

    @staticmethod
    def _bindings(rows: List[dict]) -> dict:
        out = []
        for r in rows:
            out.append({k: {"type": "literal", "value": str(v)}
                        for k, v in r.items() if v is not None})
        return {"results": {"bindings": out}}

    async def seed_chunk_relations(self, graph_name: str, values: str, limit: int):
        """청크 1홉 관계 시드. label/sourceChunk(리터럴)가 있어야 성립한다."""
        chunks = [_unescape(v) for v in re.findall(r'"((?:[^"\\]|\\.)*)"', values or "")]
        if not chunks:
            return self._bindings([])
        g = _q(graph_name)
        clist = ",".join(f'"{_q(c)}"' for c in chunks)
        # ⚠️ ArcadeDB 한계: 관계패턴 뒤의 `OPTIONAL MATCH (p {uri:r.p})` 가 r 을 참조하면
        #    조인이 조용히 실패해 p.label 이 항상 null 이 된다(실측 확인, 일반 MATCH 는 정상).
        #    Fuseki 의 OPTIONAL 의미를 보존하려고 두 갈래로 나눠 합친다:
        #      (1) 술어 label 이 있는 행 — 일반 MATCH 조인
        #      (2) 술어 label 이 없는 행 — pLabel=null (원본 SPARQL 의 OPTIONAL 미바인딩)
        head = (
            f'MATCH (s:Resource)-[r:REL {{g:"{g}"}}]->(o:Resource) '
            f'WHERE s.sourceChunk IS NOT NULL AND any(c IN s.sourceChunk WHERE c IN [{clist}]) '
            f'  AND s.label IS NOT NULL AND o.label IS NOT NULL '
        )
        with_label = await self._cmd(
            head + 'WITH s, o, r MATCH (p:Resource {uri:r.p}) WHERE p.label IS NOT NULL '
            'UNWIND s.label AS sLabel UNWIND p.label AS pLabel UNWIND o.label AS oLabel '
            f'RETURN DISTINCT sLabel, pLabel, oLabel LIMIT {int(limit)}'
        ) or []
        remaining = int(limit) - len(with_label)
        no_label = []
        if remaining > 0:
            # 술어 label 이 없는 행: 술어 URI 를 모아 label 보유 집합과 차집합(순수 Cypher).
            labeled = await self._cmd(
                f'MATCH ()-[r:REL {{g:"{g}"}}]->() WITH DISTINCT r.p AS p '
                'MATCH (n:Resource {uri:p}) WHERE n.label IS NOT NULL RETURN p'
            ) or []
            known = {row["p"] for row in labeled if row.get("p")}
            if known:
                excl = ",".join(f'"{_q(u)}"' for u in known)
                cond = f'AND NOT r.p IN [{excl}] '
            else:
                cond = ""
            no_label = await self._cmd(
                head + f'{cond}'
                'UNWIND s.label AS sLabel UNWIND o.label AS oLabel '
                f'RETURN DISTINCT sLabel, oLabel LIMIT {remaining}'
            ) or []
        return self._bindings(with_label + no_label)

    async def predicate_labels(self, graph_name: str):
        """술어 + 라벨. 원본 SPARQL 은 `?p rdfs:label ?pl` 필수 패턴이라 label 없는 술어는 제외."""
        g = _q(graph_name)
        res = await self._cmd(
            f'MATCH ()-[r:REL {{g:"{g}"}}]->() WITH DISTINCT r.p AS p '
            f'MATCH (n:Resource {{uri:p}}) WHERE n.label IS NOT NULL '
            f'UNWIND n.label AS pl RETURN DISTINCT p, pl'
        )
        return self._bindings(res or [])

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
