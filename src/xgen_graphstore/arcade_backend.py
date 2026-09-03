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
# 검색 의미(제외 술어·핀 파싱·RDF 상수)는 백엔드 공통 — ntriples 가 단일 출처.
from xgen_graphstore.ntriples import (  # noqa: E402
    NS_DOMAIN as _NS_DOMAIN,
    EXCLUDED_PREDS as _EXCLUDED_PREDS,
    OWL_CLASS as _OWL_CLASS,
    OWL_EQUIVALENT_CLASS as _OWL_EQUIVALENT_CLASS,
    RDF_TYPE as _RDF_TYPE,
    RDFS_SUBCLASS as _RDFS_SUBCLASS,
    parse_pin as _parse_pin,
)


def _q(s: str) -> str:
    """Cypher 이중따옴표 리터럴 이스케이프(PoC — 파라미터 대신)."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


_COOC_URI = _NS_DOMAIN + "coOccursWith"


class ArcadeBackend:
    """OntologyStore 계약의 ArcadeDB 구현(PoC). HTTP command API(Cypher)."""

    BACKEND_NAME = "arcade"
    # 리소스-트리플 CRUD + fulltext 검색(DEBTS §A 이식).
    #
    # ⚠️⚠️ **한국어 recall 한계(실측)**: ArcadeDB FULL_TEXT 의 CONTAINSTEXT 는 토큰(공백) 기반이라
    #   Fuseki/Neo4j 의 CJK bigram 과 매칭 특성이 다르다. 시드 선택 실측(Fuseki 기준 recall):
    #     '한국은행'   fuseki 47건 → neo4j 100% · **arcade 2%**
    #     '대학교'     fuseki 57건 → neo4j  68% · **arcade 19%**
    #     'University' fuseki 60건 → neo4j  98% · arcade 98%   ← 영어는 동등
    #   즉 **영어는 등가, 한국어 부분매칭은 크게 열세**다. 능력은 보유하지만 품질은 동등하지 않다.
    #   한국어 코퍼스에서 fulltext 가 중요한 배포에는 이 백엔드를 쓰면 안 된다(정본 §15 실측).
    #   해소하려면 ArcadeDB 측 한국어 분석기(n-gram) 설정이 필요하다 — 별도 과제.
    #
    # named-graph/owl-as-data/ttl/raw/그래프알고리즘(OLAP 필요)은 미보유.
    CAPABILITIES = frozenset({Capability.CORE_TRIPLE_RW, Capability.FULLTEXT_SEARCH})

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

    # ── fulltext 검색 (DEBTS §A) ──
    #
    # ⚠️ ArcadeDB 제약(실측): FULL_TEXT 인덱스는 **STRING 프로퍼티에만** 걸린다. 우리는 RDF
    #    다중값 보존을 위해 label 을 LIST 로 저장하므로 직접 인덱싱이 불가하다.
    #    → 검색 전용 STRING 필드 `labelText`(label 의 대표값)를 두고 거기에 FULL_TEXT 인덱스.
    # ⚠️ 또한 ArcadeDB 의 CONTAINSTEXT 는 **토큰(공백) 기반**이라 Fuseki/Neo4j 의 CJK bigram 과
    #    매칭 특성이 다르다("한국은행"으로 "조흥은행"이 안 걸린다). 한국어 부분매칭 recall 이
    #    구조적으로 낮다 — 이식으로 없앨 수 없는 엔진 차이이며 실측으로 정량화한다.

    async def ensure_fulltext_index(self) -> bool:
        """검색용 labelText(STRING) + FULL_TEXT 인덱스 준비(멱등). label 리스트의 대표값을 채운다."""
        for cmd in (
            "CREATE PROPERTY Resource.labelText STRING",
            "CREATE INDEX ON Resource (labelText) FULL_TEXT",
        ):
            try:
                await self._cmd(cmd, language="sql")
            except Exception:
                pass  # 이미 존재하면 진행(멱등)
        # label → labelText 동기화. 리스트 대표값(첫 값)만 색인 대상이 된다.
        await self._cmd(
            "UPDATE Resource SET labelText = label[0] WHERE label IS NOT NULL", language="sql")
        return True

    async def _ft_nodes(self, terms: str, top_n: int) -> List[str]:
        """text:query 등가 — labelText 전문검색 상위 N개 uri."""
        if not terms or not terms.strip():
            return []
        res = await self._cmd(
            # 리터럴을 큰따옴표로 둔다 — _q 는 `\\`·`"` 만 막는 **큰따옴표용**이라
            # 작은따옴표 리터럴에 쓰면 검색어로 질의가 변조된다(실측: 매칭 0인 낱말도 30행).
            f'SELECT uri FROM Resource WHERE labelText CONTAINSTEXT "{_q(terms)}" '
            f"LIMIT {int(top_n)}", language="sql")
        return [r["uri"] for r in (res or []) if r.get("uri")]

    def _uri_list(self, uris: List[str]) -> str:
        return ",".join(f'"{_q(u)}"' for u in uris)

    async def _seed_relations(self, graph_name: str, seeds: List[str], limit: int,
                              both_ends: bool):
        """connectivity(양끝)/broad(단끝) 공통 본체 — 시드 범위만 다르다."""
        if not seeds:
            return self._bindings([])
        g, slist = _q(graph_name), self._uri_list(seeds)
        excl = ",".join(f'"{_q(p)}"' for p in _EXCLUDED_PREDS)
        end_cond = (f"AND o.uri IN [{slist}] AND s.uri <> o.uri " if both_ends else "")
        head = (
            f'MATCH (s:Resource)-[r:REL {{g:"{g}"}}]->(o:Resource) '
            f'WHERE s.uri IN [{slist}] {end_cond}'
            f'  AND s.label IS NOT NULL AND o.label IS NOT NULL AND NOT r.p IN [{excl}] '
        )
        # ArcadeDB 는 관계패턴 뒤 OPTIONAL MATCH 가 r 을 참조하면 조인이 조용히 실패한다.
        # → label 보유/미보유 두 갈래로 나눠 합친다(seed_chunk_relations 와 동일 우회).
        with_label = await self._cmd(
            head + 'WITH s, o, r MATCH (p:Resource {uri:r.p}) WHERE p.label IS NOT NULL '
            'UNWIND s.label AS sLabel UNWIND p.label AS pLabel UNWIND o.label AS oLabel '
            f'RETURN DISTINCT sLabel, pLabel, oLabel LIMIT {int(limit)}') or []
        remaining = int(limit) - len(with_label)
        no_label = []
        if remaining > 0:
            labeled = await self._cmd(
                f'MATCH ()-[r:REL {{g:"{g}"}}]->() WITH DISTINCT r.p AS p '
                'MATCH (n:Resource {uri:p}) WHERE n.label IS NOT NULL RETURN p') or []
            known = {row["p"] for row in labeled if row.get("p")}
            cond = f'AND NOT r.p IN [{self._uri_list(sorted(known))}] ' if known else ""
            no_label = await self._cmd(
                head + cond + 'UNWIND s.label AS sLabel UNWIND o.label AS oLabel '
                f'RETURN DISTINCT sLabel, oLabel LIMIT {remaining}') or []
        return self._bindings(with_label + no_label)

    async def seed_connectivity_relations(self, graph_name: str, terms: str, limit: int):
        """연결성 시드: 주어·목적어 **양끝** 모두 상위80 시드에 들어야 한다."""
        return await self._seed_relations(
            graph_name, await self._ft_nodes(terms, 80), limit, both_ends=True)

    async def seed_relations_broad(self, graph_name: str, terms: str, limit: int):
        """recall 폴백: **주어만** 상위60 시드에 들면 된다."""
        return await self._seed_relations(
            graph_name, await self._ft_nodes(terms, 60), limit, both_ends=False)

    async def _seed_pinned(self, graph_name: str, terms: str, pin: str, reverse: bool):
        """정밀관계(정/역) 공통 — 상위15 시드 + 술어라벨 핀."""
        seeds = await self._ft_nodes(terms, 15)
        pins = _parse_pin(pin)
        if not seeds or not pins:
            return self._bindings([])
        g, slist = _q(graph_name), self._uri_list(seeds)
        plist = ",".join(f'"{_q(p)}"' for p in pins)
        anchor = "o.uri" if reverse else "s.uri"
        rows = await self._cmd(
            f'MATCH (s:Resource)-[r:REL {{g:"{g}"}}]->(o:Resource) '
            f'WHERE {anchor} IN [{slist}] AND s.label IS NOT NULL AND o.label IS NOT NULL '
            f'WITH s, o, r MATCH (p:Resource {{uri:r.p}}) WHERE p.label IS NOT NULL '
            'UNWIND s.label AS sl UNWIND p.label AS pl UNWIND o.label AS ol '
            f'WITH sl, pl, ol WHERE pl IN [{plist}] '
            'RETURN DISTINCT sl, pl, ol LIMIT 40') or []
        return self._bindings(rows)

    async def seed_relations_by_fulltext_forward(self, graph_name: str, terms: str, pin: str):
        return await self._seed_pinned(graph_name, terms, pin, reverse=False)

    async def seed_relations_by_fulltext_reverse(self, graph_name: str, terms: str, pin: str):
        return await self._seed_pinned(graph_name, terms, pin, reverse=True)

    async def seed_classes_by_fulltext(self, graph_name: str, terms: str,
                                       mode: str = "closure"):
        """클래스 전수 시드: 상위30 시드 중 owl:Class + 인스턴스 집계(상위 3개).

        mode 는 Fuseki 와 같은 이름·기본값이다("closure"/"direct").
        동치 폐포(무방향 `*0..`)는 두 모드 공통, 이행 폐포(subClassOf)는 closure 만 —
        `queries.seed_classes_by_fulltext_query` 와 같은 구성이다.
        """
        seeds = await self._ft_nodes(terms, 30)
        if not seeds:
            return self._bindings([])
        slist = self._uri_list(seeds)
        g = _q(graph_name)
        expand = (f'MATCH (sub:Resource)-[:REL*0.. {{p:"{_RDFS_SUBCLASS}", g:"{g}"}}]->(ceq) '
                  if mode != "direct" else 'WITH c, ceq AS sub ')
        rows = await self._cmd(
            f'MATCH (c:Resource)-[:REL {{p:"{_RDF_TYPE}", g:"{g}"}}]->(:Resource {{uri:"{_OWL_CLASS}"}}) '
            f'WHERE c.uri IN [{slist}] AND c.label IS NOT NULL '
            f'MATCH (c)-[:REL*0.. {{p:"{_OWL_EQUIVALENT_CLASS}", g:"{g}"}}]-(ceq:Resource) '
            + expand +
            f'MATCH (i:Resource)-[:REL {{p:"{_RDF_TYPE}", g:"{g}"}}]->(sub) WHERE i.label IS NOT NULL '
            'WITH c, count(DISTINCT i) AS n, collect(DISTINCT i.label[0]) AS ils '
            'RETURN c.label[0] AS cl, n, ils ORDER BY n DESC LIMIT 3') or []
        out = [{"cl": r.get("cl"), "n": r.get("n"),
                "insts": " | ".join(x for x in (r.get("ils") or []) if x)} for r in rows]
        return self._bindings(out)

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
            # 0824: 정본이 정밀 SVO 와 동시출현 약관계를 슬롯 분리한다(coarse 엣지의
            # LIMIT 선점 방지). 동시출현 슬롯은 seed_chunk_cooccurrence.
            f'  AND r.p <> "{_q(_COOC_URI)}" '
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

    async def seed_chunk_cooccurrence(self, graph_name: str, values: str, limit: int):
        """동시출현 약관계 슬롯 (0824). 술어가 단일이라 pLabel 을 조회하지 않는다 —
        따라서 seed_chunk_relations 를 괴롭히던 OPTIONAL 조인 우회가 필요 없다."""
        chunks = [_unescape(v) for v in re.findall(r'"((?:[^"\\]|\\.)*)"', values or "")]
        if not chunks:
            return self._bindings([])
        g = _q(graph_name)
        clist = ",".join(f'"{_q(c)}"' for c in chunks)
        rows = await self._cmd(
            f'MATCH (s:Resource)-[r:REL {{g:"{g}"}}]->(o:Resource) '
            f'WHERE s.sourceChunk IS NOT NULL AND any(c IN s.sourceChunk WHERE c IN [{clist}]) '
            f'  AND s.label IS NOT NULL AND o.label IS NOT NULL '
            f'  AND r.p = "{_q(_COOC_URI)}" '
            'UNWIND s.label AS sLabel UNWIND o.label AS oLabel '
            f'RETURN DISTINCT sLabel, oLabel LIMIT {int(limit)}'
        ) or []
        return self._bindings(rows)

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
        from xgen_graphstore.capabilities import implemented_methods

        raise NotImplementedError(
            f"ArcadeBackend 는 '{name}' 미구현 — 능력 공백이 아니라 구현 가능·미완이다. "
            f"현재 구현: {', '.join(implemented_methods(self))}"
        )
