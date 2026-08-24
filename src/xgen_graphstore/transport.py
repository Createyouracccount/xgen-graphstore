"""xgen-graphstore transport — Apache Jena Fuseki HTTP client.

Provenance: xgen-documents service/ontology/fuseki_client.py 에서 무변경 이관
(단 call_logger 의존만 주입식 no-op CallTimer 로 치환). 출처 커밋 8a81e23.
RDF 데이터 업로드, SPARQL 쿼리, 데이터셋 관리.
"""

import asyncio
import inspect
import json
import os
import re
import unicodedata
from typing import Dict, Any, List, Optional

import httpx

from xgen_graphstore._calltimer import CallTimer


class FusekiClient:
    """Apache Jena Fuseki HTTP 클라이언트"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        dataset: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.base_url = (base_url or os.getenv("FUSEKI_URL", "http://xgen-fuseki:3030")).rstrip("/")
        self.dataset = dataset or os.getenv("FUSEKI_DATASET", "xgen")
        self.username = username or os.getenv("FUSEKI_ADMIN_USER", "admin")
        self.password = password or os.getenv("FUSEKI_ADMIN_PASSWORD", "")

        # ── 읽기/쓰기 커넥션 풀 분리 ──
        # 단일 AsyncClient 를 빌드 워커(쓰기)와 진행률/시각화 조회(읽기)가 공유하면,
        # 빌드 중 대량 UPDATE/upload 가 커넥션 풀을 점유해 폴링 읽기가 줄 서며 랙이 걸렸다.
        # 읽기 전용 클라이언트를 분리하고 풀 크기를 외부화(env)해 읽기가 쓰기에 막히지 않게 한다.
        # 매직넘버 금지 — timeout/풀 크기는 모두 env 로.
        _timeout = float(os.getenv("FUSEKI_TIMEOUT", "60.0"))
        _read_pool = int(os.getenv("FUSEKI_READ_POOL", "20"))
        _write_pool = int(os.getenv("FUSEKI_WRITE_POOL", "10"))
        self._read_client = httpx.AsyncClient(
            timeout=_timeout,
            limits=httpx.Limits(max_connections=_read_pool, max_keepalive_connections=_read_pool),
        )
        self._write_client = httpx.AsyncClient(
            timeout=_timeout,
            limits=httpx.Limits(max_connections=_write_pool, max_keepalive_connections=_write_pool),
        )
        # 하위호환 alias — 기존 쓰기/관리/업로드 호출(self._client.*)은 전부 쓰기 풀을 쓴다.
        # 읽기 경로는 sparql_query 가 self._read_client 를 명시적으로 사용한다.
        self._client = self._write_client
        # 그래프 시각화 결과 캐시 — graph_name → {"key": (graph_name, limit, triple_count), "data": {...}}
        # 그래프당 최신 1엔트리만. triple_count 가 바뀌면(빌드/incremental/관계편집) 키 불일치로
        # 자동 무효화. get_graph_data_for_visualization 참조.
        self._viz_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def _auth(self):
        return (self.username, self.password)

    @property
    def data_url(self) -> str:
        return f"{self.base_url}/{self.dataset}/data"

    @property
    def query_url(self) -> str:
        return f"{self.base_url}/{self.dataset}/query"

    @property
    def update_url(self) -> str:
        return f"{self.base_url}/{self.dataset}/update"

    async def health_check(self) -> bool:
        """Fuseki 서버 헬스 체크"""
        try:
            resp = await self._client.get(
                f"{self.base_url}/$/ping",
                auth=self._auth,
            )
            return resp.status_code == 200
        except Exception as e:
            pass
            return False

    async def ensure_dataset(self) -> bool:
        """데이터셋 존재 확인, 없으면 생성"""
        try:
            resp = await self._client.get(
                f"{self.base_url}/$/datasets/{self.dataset}",
                auth=self._auth,
            )
            if resp.status_code == 200:
                return True

            # 데이터셋 생성
            resp = await self._client.post(
                f"{self.base_url}/$/datasets",
                auth=self._auth,
                data={
                    "dbName": self.dataset,
                    "dbType": "tdb2",
                },
            )
            if resp.status_code in (200, 201):
                pass
                return True
            else:
                pass
                return False
        except Exception as e:
            pass
            return False

    async def upload_ttl(
        self,
        ttl_content: str,
        graph_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """TTL 데이터 업로드"""
        try:
            url = self.data_url
            if graph_name:
                url += f"?graph={graph_name}"

            resp = await self._client.post(
                url,
                auth=self._auth,
                content=ttl_content.encode("utf-8"),
                headers={"Content-Type": "text/turtle; charset=utf-8"},
            )

            if resp.status_code in (200, 201, 204):
                pass
                return {"success": True, "status": resp.status_code}
            else:
                pass
                return {"success": False, "error": resp.text}
        except Exception as e:
            pass
            return {"success": False, "error": str(e)}

    async def sparql_query(
        self,
        query: str,
    ) -> Dict[str, Any]:
        """SPARQL SELECT 쿼리 실행"""
        caller = _frame_caller()
        with CallTimer(
            "SPARQL",
            caller=caller,
            payload={"endpoint": self.query_url, "query": query},
        ) as t:
            try:
                resp = await self._read_client.post(
                    self.query_url,
                    auth=self._auth,
                    data={"query": query},
                    headers={"Accept": "application/sparql-results+json"},
                )

                if resp.status_code == 200:
                    data = resp.json()
                    bindings = data.get("results", {}).get("bindings", []) if isinstance(data, dict) else []
                    # 관측 로그에는 대형 결과를 통째로 싣지 않는다 — 40.8만 행 응답이
                    # ONT-CALL 로 그대로 덤프되어 로그가 라인당 수십 MB(도커 로그 179MB)로
                    # 부풀고, 직렬화 자체가 finalize 메모리 압박에 가담했다(0714 실측).
                    # rows(총 행수) + 표본 몇 행이면 디버깅 관측성은 유지된다.
                    # 반환값(data)은 손대지 않는다 — 호출자는 전체를 받는다.
                    _LOG_BINDINGS_CAP = 50
                    if len(bindings) > _LOG_BINDINGS_CAP:
                        log_data = {
                            "head": data.get("head"),
                            "results": {"bindings": bindings[:_LOG_BINDINGS_CAP]},
                            "truncated_for_log": f"{len(bindings)} rows → {_LOG_BINDINGS_CAP} 표본",
                        }
                    else:
                        log_data = data
                    t.set_result({
                        "status_code": 200,
                        "rows": len(bindings),
                        "data": log_data,
                    })
                    return data
                else:
                    t.set_result({"status_code": resp.status_code, "error": resp.text})
                    t.status = "error"
                    return {"error": resp.text}
            except Exception as e:
                t.set_result({"exception": str(e)})
                t.status = "error"
                return {"error": str(e)}

    async def sparql_update(self, update: str) -> bool:
        """SPARQL UPDATE 실행"""
        caller = _frame_caller()
        with CallTimer(
            "SPARQL_UPDATE",
            caller=caller,
            payload={"endpoint": self.update_url, "update": update},
        ) as t:
            try:
                # raw body(application/sparql-update) — form 인코딩은 한글 IRI/리터럴에서
                # "Unable to parse form content" 로 깨짐. raw 가 표준이며 UTF-8 안전.
                resp = await self._client.post(
                    self.update_url,
                    auth=self._auth,
                    content=update.encode("utf-8"),
                    headers={"Content-Type": "application/sparql-update; charset=utf-8"},
                )
                ok = resp.status_code in (200, 204)
                t.set_result({"status_code": resp.status_code, "ok": ok})
                if not ok:
                    t.status = "error"
                return ok
            except Exception as e:
                t.set_result({"exception": str(e)})
                t.status = "error"
                return False

    # ── ontology-search 계보 수리 이관 (2026-08-24) ─────────────────────────
    # 출처: xgen-documents service/ontology/fuseki_client.py @ontology-search.
    # 74a322a 추출 시점의 원본은 이 3개가 없던 판본이라 누락됐다. pipeline.py 가
    # sparql_query_csv 를 10곳, compact_dataset/has_active_compact 를 빌드 루프에서
    # 호출하므로, 없으면 FusekiBackend 스왑 시 AttributeError 로 죽는다.
    async def compact_dataset(self, timeout_sec: float = 600.0) -> bool:
        """TDB2 스토어 압축(라이브 compact) — 죽은 블록 회수.

        왜 필요한가: TDB2 는 copy-on-write append-only 라 쓰기 트랜잭션마다
        수정된 B+트리 경로 전체가 파일 끝에 복사되고 옛 페이지는 죽은 채 남는다
        (compact 전까지 절대 회수 안 됨). 그룹 단위 누적 빌드(201 그룹 × 그룹당
        1+ 트랜잭션)에서는 트리가 커질수록 트랜잭션당 복사량도 커져서 팽창이
        가속된다 — 실측: 실데이터 1.4GB 그래프가 한 번의 전체 빌드로 121GB 까지
        부풀어 디스크 풀로 빌드가 죽었다(2026-07-15). 빌드 중 주기 호출로 스토어를
        작게 유지하면 트랜잭션당 비용도 작게 유지돼 팽창이 선형 이하로 억제된다.

        deleteOld=true: 압축 완료 즉시 구세대(Data-000N) 삭제 — 이게 없으면
        구세대가 남아 디스크는 그대로다. 완료까지 폴링해 동기적으로 기다린다
        (다음 그룹의 쓰기가 compact 와 경합하지 않도록).
        """
        try:
            resp = await self._client.post(
                f"{self.base_url}/$/compact/{self.dataset}",
                params={"deleteOld": "true"},
                auth=self._auth,
            )
            if resp.status_code not in (200, 201):
                return False
            task_id = (resp.json() or {}).get("taskId")
            if not task_id:
                return False
            waited = 0.0
            poll = 2.0
            while waited < timeout_sec:
                await asyncio.sleep(poll)
                waited += poll
                st = await self._read_client.get(
                    f"{self.base_url}/$/tasks/{task_id}", auth=self._auth
                )
                if st.status_code == 200 and (st.json() or {}).get("finished"):
                    return bool((st.json() or {}).get("success", True))
            return False  # 타임아웃 — compact 는 서버에서 계속 돌 수 있음(best-effort)
        except Exception:
            return False

    async def has_active_compact(self) -> bool:
        """서버에 진행 중 Compact 태스크가 있는지 — 0814 사고: 타임아웃으로 False
        반환 후에도 서버는 compact 를 계속 돌리며 쓰기 락을 쥔다. 이 상태에서
        대량쓰기를 얹으면 전부 무증상 타임아웃(사이드카 60s 전멸 실측). 후속
        쓰기 단계는 이 메서드로 배타성을 확인해야 한다."""
        try:
            st = await self._read_client.get(f"{self.base_url}/$/tasks", auth=self._auth)
            if st.status_code != 200:
                return False
            return any(t.get("task") == "Compact" and not t.get("finished")
                       for t in (st.json() or []))
        except Exception:
            return False

    async def sparql_query_csv(self, query: str, timeout: Optional[float] = None) -> str:
        """SPARQL SELECT 를 CSV 텍스트로 — 대용량 결과 전용.

        JSON bindings 는 행당 dict 오버헤드가 커서 수십만 행(클래스 필터의 44만
        클래스 통계)에서 GB 급 메모리 → 컨테이너 OOM kill 실측. CSV 는 같은 결과가
        수십 MB 텍스트. 실패 시 빈 문자열(호출측 비치명 처리)."""
        try:
            resp = await self._read_client.post(
                self.query_url,
                auth=self._auth,
                data={"query": query},
                headers={"Accept": "text/csv"},
                timeout=timeout,
            )
            return resp.text if resp.status_code == 200 else ""
        except Exception:
            return ""

    async def clean_subclassof_noise(self, graph_name: Optional[str] = None) -> bool:
        """subClassOf 정제 — 부모가 클래스가 아니라 *속성(관계)*인 잘못된 is-a 엣지를 제거.
        추출기가 관계명을 subClassOf 부모로 잘못 박는 노이즈(relevantRegion·applicationCriteria 등)를
        차단한다. "이어져있다고 다 상속 아니다" — 진짜 is-a 만 남겨 상속/추론/뷰의 신뢰성 확보.
        빌드에서 상속 구체화 *직전*에 호출(노이즈 전파 방지)."""
        pfx = ("PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#> "
               "PREFIX owl:<http://www.w3.org/2002/07/owl#> ")
        body = ("?c rdfs:subClassOf ?p . ?p a ?t . "
                "FILTER(?t IN (owl:ObjectProperty, owl:DatatypeProperty))")
        if graph_name:
            upd = f"{pfx} DELETE {{ GRAPH <{graph_name}> {{ ?c rdfs:subClassOf ?p }} }} WHERE {{ GRAPH <{graph_name}> {{ {body} }} }}"
        else:
            upd = f"{pfx} DELETE {{ ?c rdfs:subClassOf ?p }} WHERE {{ {body} }}"
        return await self.sparql_update(upd)

    async def materialize_property_inheritance(self, graph_name: Optional[str] = None) -> bool:
        """자동 상속(P0) — subClassOf 직계 부모의 속성(domain)을 자식 클래스에 전파(구체화).
        OWL/RDFS 상 entailment 지만, 추론기 없이 검색/시각화가 즉시 활용하도록 명시 트리플로 박는다.
        직계(1-level)만 — 밀집 계층서 transitive(subClassOf+)는 폭증(masahoe 실측 +13k, 클래스당 584속성).
        효과: 검색=자식 클래스 질의에 부모 속성 포함(완전성), 뷰=계층 속성이 자식에도 노출.
        """
        pfx = ("PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#> "
               "PREFIX owl:<http://www.w3.org/2002/07/owl#> ")
        # 유효 is-a 게이트: "이어져있다고 다 상속 아니다". 부모가 진짜 클래스여야 하고,
        # *속성(관계)으로도 타입된 부모는 제외*(추출 노이즈: relevantRegion·applicationCriteria 처럼
        # 관계명이 subClassOf 부모로 잘못 박힌 경우 상속 전파 차단). 도메인 하드코딩 아님 — 구조 검증.
        inner = ("?child rdfs:subClassOf ?parent . ?child a owl:Class . ?parent a owl:Class . "
                 "FILTER NOT EXISTS { ?parent a owl:ObjectProperty } "
                 "FILTER NOT EXISTS { ?parent a owl:DatatypeProperty } "
                 "?prop rdfs:domain ?parent . ?prop a ?pt . "
                 "FILTER(?pt IN (owl:DatatypeProperty, owl:ObjectProperty)) "
                 "FILTER NOT EXISTS { ?prop rdfs:domain ?child }")
        if graph_name:
            upd = f"{pfx} INSERT {{ GRAPH <{graph_name}> {{ ?prop rdfs:domain ?child }} }} WHERE {{ GRAPH <{graph_name}> {{ {inner} }} }}"
        else:
            upd = f"{pfx} INSERT {{ ?prop rdfs:domain ?child }} WHERE {{ {inner} }}"
        return await self.sparql_update(upd)

    async def clear_graph(self, graph_name: Optional[str] = None) -> bool:
        """그래프 데이터 삭제.

        SILENT 필수: 대상 그래프가 아직 없으면 Fuseki 가 "No such graph" 로 500 을 반환한다.
        빌드 파이프라인은 시작 시 항상 clear 를 호출하므로, SILENT 가 없으면
        해당 컬렉션의 *첫 빌드* 가 무조건 실패한다(온톨로지 트리플 0건).
        """
        if graph_name:
            return await self.sparql_update(f"CLEAR SILENT GRAPH {self._graph_ref(graph_name)}")
        else:
            return await self.sparql_update("CLEAR ALL")

    @staticmethod
    def _graph_ref(graph_name: str) -> str:
        """사용자 입력이 섞인 graph IRI를 SPARQL 구문 경계 밖으로 못 나가게 한다."""
        forbidden = '<>"{}|^`\\'
        if (
            not graph_name.startswith(("http://", "https://"))
            or any(
                ch in forbidden
                or ch.isspace()
                or unicodedata.category(ch) == "Cc"
                for ch in graph_name
            )
        ):
            raise ValueError("Invalid graph IRI")
        return f"<{graph_name}>"

    _INGEST_COMMIT_GRAPH = "https://w3id.org/xgen/ontology/ingest-commits"

    @classmethod
    def _ingest_commit_marker_ref(cls, commit_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._~-]+", str(commit_id or "")):
            raise ValueError("Invalid ingest commit id")
        return f"<urn:xgen:ontology-ingest-commit:{commit_id}>"

    async def get_ingest_commit_marker(self, commit_id: str) -> Optional[bool]:
        """True/False는 확인 결과, None은 Fuseki 조회 불가(안전하게 판정 보류)."""
        marker = self._ingest_commit_marker_ref(commit_id)
        control = self._graph_ref(self._INGEST_COMMIT_GRAPH)
        result = await self.sparql_query(
            f"ASK {{ GRAPH {control} {{ {marker} "
            "<https://w3id.org/xgen-domain#ingestCommitted> true } }"
        )
        value = result.get("boolean") if isinstance(result, dict) else None
        return value if isinstance(value, bool) else None

    async def commit_staged_graph(
        self,
        *,
        target_graph: str,
        staging_graph: str,
        source_id: str,
        replace_source: bool,
        commit_id: str,
    ) -> bool:
        """staging을 target에 한 SPARQL Update 요청으로 반영하고 비운다.

        full run은 exact sourceId 또는 구버전 sourceChunk prefix로 찾은 source 소유
        individual의 outgoing을 교체하고 snapshot에서 사라진 URI의 incoming만 지운다.
        incremental은 staging에서 exact sourceId marker가 확인된 실제 행 URI의
        outgoing만 교체한다. FK placeholder는 sourceId marker가 없어 타 source
        데이터를 지우지 않는다.
        """
        target = self._graph_ref(target_graph)
        staging = self._graph_ref(staging_graph)
        control = self._graph_ref(self._INGEST_COMMIT_GRAPH)
        marker = self._ingest_commit_marker_ref(commit_id)
        source_literal = json.dumps(str(source_id), ensure_ascii=False)
        legacy_prefix_literal = json.dumps(f"{source_id}:", ensure_ascii=False)
        prefixes = (
            "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
            "PREFIX xgen: <https://w3id.org/xgen-domain#>\n"
        )
        marker_pattern = f"GRAPH {control} {{ {marker} xgen:ingestCommitted true . }}"
        marker_guard = f"FILTER NOT EXISTS {{ {marker_pattern} }}"

        if replace_source:
            delete_operations = [f"""
DELETE {{ GRAPH {target} {{ ?inSubject ?inPredicate ?entity . }} }}
WHERE {{
  GRAPH {target} {{
    ?entity a owl:NamedIndividual .
    {{
      ?entity xgen:sourceId ?exactSource .
      FILTER(STR(?exactSource) = {source_literal})
    }} UNION {{
      ?entity xgen:sourceChunk ?legacySource .
      FILTER(STRSTARTS(STR(?legacySource), {legacy_prefix_literal}))
    }}
    ?inSubject ?inPredicate ?entity .
  }}
  FILTER NOT EXISTS {{
    GRAPH {staging} {{
      ?entity xgen:sourceId ?stagedSource .
      FILTER(STR(?stagedSource) = {source_literal})
    }}
  }}
  {marker_guard}
}}
""", f"""
DELETE {{ GRAPH {target} {{ ?entity ?outPredicate ?outObject . }} }}
WHERE {{
  GRAPH {target} {{
    ?entity a owl:NamedIndividual .
    {{
      ?entity xgen:sourceId ?exactSource .
      FILTER(STR(?exactSource) = {source_literal})
    }} UNION {{
      ?entity xgen:sourceChunk ?legacySource .
      FILTER(STRSTARTS(STR(?legacySource), {legacy_prefix_literal}))
    }}
    ?entity ?outPredicate ?outObject .
  }}
  {marker_guard}
}}
"""]
        else:
            delete_operations = [f"""
DELETE {{ GRAPH {target} {{ ?entity ?outPredicate ?outObject . }} }}
WHERE {{
  GRAPH {staging} {{
    ?entity a owl:NamedIndividual ; xgen:sourceId ?stagedSource .
    FILTER(STR(?stagedSource) = {source_literal})
  }}
  GRAPH {target} {{ ?entity ?outPredicate ?outObject . }}
  {marker_guard}
}}
"""]

        copy_operation = f"""
INSERT {{ GRAPH {target} {{ ?stagedSubject ?stagedPredicate ?stagedObject . }} }}
WHERE {{
  GRAPH {staging} {{ ?stagedSubject ?stagedPredicate ?stagedObject . }}
  {marker_guard}
}}
"""
        # 동일 RDF triple의 INSERT DATA는 멱등이다. 바인딩 없는
        # `WHERE { FILTER NOT EXISTS ... }`는 Jena에서 빈 solution으로 평가될 수 있어
        # HTTP 성공인데 marker가 생기지 않는 경우가 있으므로 DATA update를 사용한다.
        marker_operation = f"""
INSERT DATA {{ GRAPH {control} {{ {marker} xgen:ingestCommitted true . }} }}
"""
        operations = delete_operations + [
            copy_operation,
            marker_operation,
            f"CLEAR SILENT GRAPH {staging}",
        ]
        update = prefixes + ";\n".join(operation.strip() for operation in operations)
        committed = await self.sparql_update(update)
        if committed:
            # triple_count가 우연히 같아도 시각화 캐시가 stale하지 않게 명시 무효화.
            self._viz_cache.pop(target_graph, None)
            self._viz_cache.pop(staging_graph, None)
        return committed

    # type 트리플 카운트 — POS 인덱스 직접 매칭 (full URI, PREFIX 처리 0).
    # DISTINCT 안 씀 — RDF 의미상 같은 (s,p,o) 트리플 중복 적재 안 됨 (TDB2 자동 dedup).
    # 같은 ?x 가 owl:Class type 트리플 두 번 가질 일 없으므로 COUNT(*) = DISTINCT 와 동치.
    _RDF_TYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
    _OWL_CLASS = "<http://www.w3.org/2002/07/owl#Class>"
    _OWL_OBJECT_PROPERTY = "<http://www.w3.org/2002/07/owl#ObjectProperty>"
    _OWL_DATATYPE_PROPERTY = "<http://www.w3.org/2002/07/owl#DatatypeProperty>"

    async def _count_type(self, graph_name: str, type_uri: str) -> int:
        """특정 type 의 인스턴스 수 — POS 인덱스 한 번 lookup."""
        query = (
            f"SELECT (COUNT(*) AS ?n) WHERE {{ "
            f"GRAPH <{graph_name}> {{ ?s {self._RDF_TYPE} {type_uri} }} }}"
        )
        result = await self.sparql_query(query)
        try:
            bindings = result.get("results", {}).get("bindings", [])
            if bindings:
                return int(bindings[0]["n"]["value"])
        except (KeyError, IndexError, ValueError):
            pass
        return 0

    async def count_classes(self, graph_name: str) -> int:
        """그래프의 owl:Class 인스턴스 수 (TBox 클래스).

        /stats 의 class_count 진실 원본 — Fuseki 가 그래프 진실 (PG ontology_schema
        는 메타데이터 캐시). PG single-source 의 stale 문제 회피 (rebuild=true 가
        PG 청소하면 stats=0 응답하던 함정 해소).
        """
        return await self._count_type(graph_name, self._OWL_CLASS)

    async def count_properties(self, graph_name: str) -> int:
        """그래프의 owl:ObjectProperty + owl:DatatypeProperty 인스턴스 수.

        ObjectProperty 와 DatatypeProperty 각각 분리 호출 후 Python 합산 —
        UNION 의 hash 비용 회피. 두 호출 다 POS 인덱스 한 번씩 lookup 이라 빠름.
        """
        op = await self._count_type(graph_name, self._OWL_OBJECT_PROPERTY)
        dp = await self._count_type(graph_name, self._OWL_DATATYPE_PROPERTY)
        return op + dp

    async def get_triple_count(self, graph_name: Optional[str] = None) -> int:
        """트리플 수 조회.

        P0: 진행 상태(_AttemptedChunkMarker)는 더 이상 콘텐츠 그래프에 박지 않고
        PG 카운터(ontology_build_jobs.processed_chunks)로 추적하므로, 카운트에서
        마커를 걸러내던 FILTER 가 불필요해졌다 → 단순 COUNT.
        """
        if graph_name:
            query = (
                f"SELECT (COUNT(*) as ?count) WHERE {{ "
                f"GRAPH <{graph_name}> {{ ?s ?p ?o }} }}"
            )
        else:
            query = f"SELECT (COUNT(*) as ?count) WHERE {{ ?s ?p ?o }}"

        result = await self.sparql_query(query)
        try:
            bindings = result.get("results", {}).get("bindings", [])
            if bindings:
                return int(bindings[0]["count"]["value"])
        except (KeyError, IndexError, ValueError):
            pass
        return 0

    async def get_graph_data_for_visualization(
        self,
        graph_name: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        """그래프 시각화용 노드/엣지 데이터 조회

        3단계로 구성:
        1. 클래스 노드 + 한글 라벨 수집
        2. ObjectProperty의 domain→range 관계를 클래스 간 엣지로 변환
        3. 인스턴스 노드 + 인스턴스 간 관계 추가

        결과는 triple_count 기반으로 캐시한다. 빌드/incremental/관계편집으로 트리플 수가
        바뀌지 않는 한, 무거운 6쿼리(실측 인스턴스-관계 단독 2.4s+, 전체 ~3.4s)를 건너뛰고
        즉시 반환 → 컬렉션 재진입 시 "열 때마다 로딩" 제거. count 는 빠른 COUNT 쿼리.
        """
        # --- 캐시 조회 ---
        try:
            triple_count = await self.get_triple_count(graph_name)
        except Exception:
            triple_count = None
        cache_key = (graph_name, limit, triple_count)
        if triple_count is not None:
            cached = self._viz_cache.get(graph_name)
            if cached and cached.get("key") == cache_key:
                return cached["data"]

        gc = f"GRAPH <{graph_name}>" if graph_name else ""
        wrap = lambda body: f"{{ {gc} {{ {body} }} }}" if gc else f"{{ {body} }}"

        nodes = {}
        edges = []
        seen_edges = set()

        # 스키마(TBox) 쿼리 안전망 상한 — 보통 클래스/속성 수십 개라 캡에 안 닿지만,
        # 병리적 그래프에서 무제약 스캔이 30초 abort 까지 가는 걸 차단.
        schema_limit = max(limit * 4, 2000)

        prefixes = """
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        """

        # --- 6개 쿼리 정의 (서로 독립 read) ---
        # 1) 클래스 노드 (한글 label 우선)
        class_query = f"""{prefixes}
        SELECT ?cls ?label WHERE {wrap('''
            ?cls rdf:type owl:Class .
            ?cls rdfs:label ?label .
            FILTER(LANG(?label) = "ko" || LANG(?label) = "")
        ''')}
        LIMIT {schema_limit}
        """
        # 2) subClassOf 계층
        hierarchy_query = f"""{prefixes}
        SELECT ?child ?parent WHERE {wrap('''
            ?child rdfs:subClassOf ?parent .
            ?child rdf:type owl:Class .
            ?parent rdf:type owl:Class .
        ''')}
        LIMIT {schema_limit}
        """
        # 3) ObjectProperty domain→range
        prop_query = f"""{prefixes}
        SELECT ?prop ?domain ?range WHERE {wrap('''
            ?prop rdf:type owl:ObjectProperty .
            ?prop rdfs:domain ?domain .
            ?prop rdfs:range ?range .
            ?domain rdf:type owl:Class .
            ?range rdf:type owl:Class .
        ''')}
        LIMIT {schema_limit}
        """
        # 4) DatatypeProperty
        dprop_query = f"""{prefixes}
        SELECT ?prop ?propLabel ?domain WHERE {wrap('''
            ?prop rdf:type owl:DatatypeProperty .
            ?prop rdfs:domain ?domain .
            ?domain rdf:type owl:Class .
            OPTIONAL {{ ?prop rdfs:label ?propLabel . FILTER(LANG(?propLabel) = "ko" || LANG(?propLabel) = "") }}
        ''')}
        LIMIT {schema_limit}
        """
        # 5) 인스턴스 노드 — 라벨 OPTIONAL (라벨 없는 인스턴스도 시각화 포함).
        # 라벨이 REQUIRED 면 KG 빌더 path 중 라벨 누락된 잔존 인스턴스가 결과에서 빠져
        # 그 인스턴스 관련 instanceOf / 인스턴스간 관계 엣지도 일괄 누락 (s/o not in nodes).
        # OPTIONAL + URI local name fallback 으로 모든 인스턴스 보장.
        #
        # ⚠️성능: `?cls rdf:type owl:Class` 검증을 넣으면 인스턴스(15만)×클래스(44만)
        # 교차 조인으로 대용량 그래프에서 폭발한다(mixed20k 실측 83s→프론트 30s
        # 타임아웃→빈 그래프). 이 검증은 불필요 — 인스턴스의 rdf:type 은 도메인 클래스
        # (인물/기관/…)와 owl:NamedIndividual 뿐이라, 후자만 FILTER 로 빼면 나머지가 곧
        # 도메인 클래스다. 검증 제거로 83s→0.0s (실측, 결과 동일). NamedIndividual 자기
        # 타입은 시각화에서 클래스 노드가 아니므로 제외한다.
        inst_query = f"""{prefixes}
        SELECT ?inst ?label ?cls WHERE {wrap('''
            ?inst rdf:type owl:NamedIndividual .
            ?inst rdf:type ?cls .
            FILTER(?cls != owl:NamedIndividual)
            OPTIONAL {{ ?inst rdfs:label ?label . FILTER(LANG(?label) = "ko" || LANG(?label) = "") }}
        ''')}
        LIMIT {limit}
        """
        # 6) 인스턴스 간 관계 (도메인 특화 프로퍼티)
        # ⚠️성능: 이전엔 s·o 를 owl:NamedIndividual 로 잡고 ?s ?p ?o (자유 술어)를
        # FILTER 로 걸러, 15만 인스턴스의 모든 트리플을 스캔해 대용량에서 ~16s 걸렸다.
        # 술어를 owl:ObjectProperty 로 한정하면(관계는 정의상 ObjectProperty) 관계
        # 트리플만 조회 → 16s→0.5s (실측, 결과 동일). s 는 인스턴스 검증 유지, o 는
        # ObjectProperty range 라 자동 인스턴스(무검증으로 조인 1개 절약).
        inst_rel_query = f"""{prefixes}
        SELECT ?s ?p ?o WHERE {wrap('''
            ?p rdf:type owl:ObjectProperty .
            ?s ?p ?o .
            ?s rdf:type owl:NamedIndividual .
        ''')}
        LIMIT {limit}
        """

        # --- 병렬 실행 ---
        # 기존 직렬 6왕복(실측 인스턴스-관계 단독 2.37s)을 asyncio.gather 로 동시 발사 →
        # 벽시계가 "6개 합" 에서 "가장 느린 1개" 수준으로 단축. 후처리는 아래에서 기존과
        # 동일한 고정 순서로 수행해 노드/엣지 dedup 결정성 유지.
        (
            class_result,
            hierarchy_result,
            prop_result,
            dprop_result,
            inst_result,
            inst_rel_result,
        ) = await asyncio.gather(
            self.sparql_query(class_query),
            self.sparql_query(hierarchy_query),
            self.sparql_query(prop_query),
            self.sparql_query(dprop_query),
            self.sparql_query(inst_query),
            self.sparql_query(inst_rel_query),
        )

        # --- 1) 클래스 노드 수집 (한글 label 우선) ---
        for b in class_result.get("results", {}).get("bindings", []):
            uri = b["cls"]["value"]
            label = b["label"]["value"]
            if uri not in nodes:
                nodes[uri] = {"id": uri, "label": label, "type": "concept"}

        # --- 2) subClassOf → 계층 관계 엣지 ---
        for b in hierarchy_result.get("results", {}).get("bindings", []):
            child_uri = b["child"]["value"]
            parent_uri = b["parent"]["value"]
            edge_key = (child_uri, parent_uri, "subClassOf")
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append({
                    "source": child_uri,
                    "target": parent_uri,
                    "relation_type": "subClassOf",
                    "predicate_uri": "http://www.w3.org/2000/01/rdf-schema#subClassOf",
                    "edge_weight": 1.0,
                    "editable": True,
                })

        # --- 3) ObjectProperty domain→range → 클래스 간 엣지 ---
        for b in prop_result.get("results", {}).get("bindings", []):
            domain_uri = b["domain"]["value"]
            range_uri = b["range"]["value"]
            prop_uri = b["prop"]["value"]
            prop_label = prop_uri.split("#")[-1] if "#" in prop_uri else prop_uri.split("/")[-1]

            edge_key = (domain_uri, range_uri, prop_label)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                # ObjectProperty class-level edge: 시각화 source=domain, target=range지만
                # 실제 트리플은 (prop, rdfs:domain, domain) + (prop, rdfs:range, range)로
                # 단일 트리플 매핑 불가 → editable=False (스키마 정의 변경은 별도 API)
                edges.append({
                    "source": domain_uri,
                    "target": range_uri,
                    "relation_type": prop_label,
                    "predicate_uri": prop_uri,
                    "edge_weight": 1.0,
                    "editable": False,
                    "edge_kind": "objectProperty_schema",
                })

        # --- 4) DatatypeProperty → 클래스에 속성 노드 연결 ---
        for b in dprop_result.get("results", {}).get("bindings", []):
            prop_uri = b["prop"]["value"]
            domain_uri = b["domain"]["value"]
            prop_label = b.get("propLabel", {}).get("value", "")
            if not prop_label:
                prop_label = prop_uri.split("#")[-1] if "#" in prop_uri else prop_uri.split("/")[-1]

            # 속성을 별도 노드로 추가
            if prop_uri not in nodes:
                nodes[prop_uri] = {"id": prop_uri, "label": prop_label, "type": "property"}
            edge_key = (domain_uri, prop_uri, "datatypeProperty")
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                # DatatypeProperty class-level edge: 시각화 source=class, target=prop이지만
                # 실제 트리플은 (prop, rdfs:domain, class)로 방향 반대 + 별개 트리플들 존재
                # → editable=False (스키마 속성 정의 삭제는 별도 API)
                edges.append({
                    "source": domain_uri,
                    "target": prop_uri,
                    "relation_type": "datatypeProperty",
                    "predicate_uri": prop_uri,
                    "edge_weight": 0.7,
                    "editable": False,
                    "edge_kind": "datatypeProperty_schema",
                })

        # --- 5) 인스턴스 노드 + 인스턴스 간 관계 ---
        inst_class_map = {}
        for b in inst_result.get("results", {}).get("bindings", []):
            uri = b["inst"]["value"]
            # 라벨 OPTIONAL — 없으면 URI local name fallback (instanceOf 엣지 보장).
            label = b.get("label", {}).get("value", "")
            if not label:
                label = uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
            cls_uri = b.get("cls", {}).get("value", "")
            if uri not in nodes:
                nodes[uri] = {"id": uri, "label": label, "type": "instance"}
            inst_class_map[uri] = cls_uri
            # 인스턴스 → 클래스 연결 (cls가 노드에 없으면 추가)
            if cls_uri:
                if cls_uri not in nodes:
                    cls_label = cls_uri.split("#")[-1] if "#" in cls_uri else cls_uri.split("/")[-1]
                    nodes[cls_uri] = {"id": cls_uri, "label": cls_label, "type": "concept"}
            if cls_uri:
                edge_key = (uri, cls_uri, "instanceOf")
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source": uri,
                        "target": cls_uri,
                        "relation_type": "instanceOf",
                        "predicate_uri": "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
                        "edge_weight": 0.5,
                        "editable": True,
                    })

        # 인스턴스 간 관계 (도메인 특화 프로퍼티)
        # s 또는 o 가 inst_query 결과에 없어도 (LIMIT 초과 또는 다른 graph 영역) 관계 자체는
        # 그래프 진실이므로 노드 자동 추가 후 엣지 박는다. 옛 동작은 누락된 한쪽 때문에 관계
        # 통째로 버려서 시각화에 "고립된 클러스터" 같이 보였음.
        for b in inst_rel_result.get("results", {}).get("bindings", []):
            s_uri = b["s"]["value"]
            o_uri = b["o"]["value"]
            p_uri = b["p"]["value"]
            p_label = p_uri.split("#")[-1] if "#" in p_uri else p_uri.split("/")[-1]
            if s_uri not in nodes:
                nodes[s_uri] = {
                    "id": s_uri,
                    "label": s_uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1],
                    "type": "instance",
                }
            if o_uri not in nodes:
                nodes[o_uri] = {
                    "id": o_uri,
                    "label": o_uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1],
                    "type": "instance",
                }
            edge_key = (s_uri, o_uri, p_label)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append({
                    "source": s_uri,
                    "target": o_uri,
                    "relation_type": p_label,
                    "predicate_uri": p_uri,
                    "edge_weight": 1.0,
                    "editable": True,
                })

        result = {
            "nodes": list(nodes.values()),
            "edges": edges,
        }
        # --- 캐시 저장 (triple_count 조회 성공 시에만) ---
        if triple_count is not None:
            self._viz_cache[graph_name] = {"key": cache_key, "data": result}
        return result

    async def get_tbox_schema(
        self,
        graph_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fuseki에서 실제 TBox(OWL 스키마)를 직접 조회한다.

        Returns:
            {
                "classes": [{"uri", "label", "parent"}],
                "object_properties": [{"uri", "label", "domain", "range"}],
                "datatype_properties": [{"uri", "label", "domain", "range"}],
                "instance_sample": [{"uri", "label", "class_label"}],
            }
        """
        gc = f"GRAPH <{graph_name}>" if graph_name else ""
        wrap = lambda body: f"{{ {gc} {{ {body} }} }}" if gc else f"{{ {body} }}"

        result = {
            "classes": [],
            "object_properties": [],
            "datatype_properties": [],
            "instance_sample": [],
        }

        # 1) 클래스 + 부모 계층
        class_q = f"""
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

        SELECT ?cls ?label ?parent ?parentLabel WHERE {wrap('''
            ?cls rdf:type owl:Class .
            OPTIONAL {{ ?cls rdfs:label ?label . FILTER(LANG(?label) = "ko" || LANG(?label) = "") }}
            OPTIONAL {{
                ?cls rdfs:subClassOf ?parent .
                ?parent rdf:type owl:Class .
                OPTIONAL {{ ?parent rdfs:label ?parentLabel . FILTER(LANG(?parentLabel) = "ko" || LANG(?parentLabel) = "") }}
            }}
        ''')}
        """
        class_res = await self.sparql_query(class_q)
        seen_cls = set()
        for b in class_res.get("results", {}).get("bindings", []):
            uri = b.get("cls", {}).get("value", "")
            if uri in seen_cls:
                continue
            seen_cls.add(uri)
            result["classes"].append({
                "uri": uri,
                "label": b.get("label", {}).get("value", ""),
                "parent": b.get("parentLabel", b.get("parent", {})).get("value", ""),
            })

        # 2) ObjectProperty + domain/range
        op_q = f"""
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

        SELECT ?prop ?label ?domainLabel ?rangeLabel WHERE {wrap('''
            ?prop rdf:type owl:ObjectProperty .
            OPTIONAL {{ ?prop rdfs:label ?label . FILTER(LANG(?label) = "ko" || LANG(?label) = "") }}
            OPTIONAL {{
                ?prop rdfs:domain ?domain .
                ?domain rdfs:label ?domainLabel . FILTER(LANG(?domainLabel) = "ko" || LANG(?domainLabel) = "")
            }}
            OPTIONAL {{
                ?prop rdfs:range ?range .
                ?range rdfs:label ?rangeLabel . FILTER(LANG(?rangeLabel) = "ko" || LANG(?rangeLabel) = "")
            }}
        ''')}
        """
        op_res = await self.sparql_query(op_q)
        for b in op_res.get("results", {}).get("bindings", []):
            result["object_properties"].append({
                "uri": b.get("prop", {}).get("value", ""),
                "label": b.get("label", {}).get("value", ""),
                "domain": b.get("domainLabel", {}).get("value", ""),
                "range": b.get("rangeLabel", {}).get("value", ""),
            })

        # 3) DatatypeProperty + domain/range(xsd type)
        dp_q = f"""
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

        SELECT ?prop ?label ?domainLabel ?range WHERE {wrap('''
            ?prop rdf:type owl:DatatypeProperty .
            OPTIONAL {{ ?prop rdfs:label ?label . FILTER(LANG(?label) = "ko" || LANG(?label) = "") }}
            OPTIONAL {{
                ?prop rdfs:domain ?domain .
                ?domain rdfs:label ?domainLabel . FILTER(LANG(?domainLabel) = "ko" || LANG(?domainLabel) = "")
            }}
            OPTIONAL {{ ?prop rdfs:range ?range }}
        ''')}
        """
        dp_res = await self.sparql_query(dp_q)
        for b in dp_res.get("results", {}).get("bindings", []):
            range_uri = b.get("range", {}).get("value", "")
            # xsd:type 추출
            if "#" in range_uri:
                range_val = "xsd:" + range_uri.split("#")[-1]
            else:
                range_val = range_uri
            result["datatype_properties"].append({
                "uri": b.get("prop", {}).get("value", ""),
                "label": b.get("label", {}).get("value", ""),
                "domain": b.get("domainLabel", {}).get("value", ""),
                "range": range_val,
            })

        # 4) 인스턴스 샘플 (클래스별 최대 3개씩)
        sample_q = f"""
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

        SELECT ?inst ?label ?classLabel WHERE {wrap('''
            ?inst rdf:type owl:NamedIndividual .
            ?inst rdfs:label ?label .
            ?inst rdf:type ?cls .
            ?cls rdf:type owl:Class .
            ?cls rdfs:label ?classLabel .
            FILTER(LANG(?label) = "ko" || LANG(?label) = "")
            FILTER(LANG(?classLabel) = "ko" || LANG(?classLabel) = "")
        ''')}
        LIMIT 50
        """
        sample_res = await self.sparql_query(sample_q)
        for b in sample_res.get("results", {}).get("bindings", []):
            result["instance_sample"].append({
                "uri": b.get("inst", {}).get("value", ""),
                "label": b.get("label", {}).get("value", ""),
                "class_label": b.get("classLabel", {}).get("value", ""),
            })

        return result

    async def close(self):
        # 읽기/쓰기 풀 모두 정리. _client 는 _write_client alias 이므로 중복 close 회피.
        await self._write_client.aclose()
        if self._read_client is not self._write_client:
            await self._read_client.aclose()


def _frame_caller() -> str:
    """호출 스택에서 fuseki_client 외부의 가장 가까운 프레임을 caller로 표기.

    예: graph_rag_operations.explore_node:347
    """
    try:
        stack = inspect.stack()
        for fr in stack[1:]:
            fname = (fr.filename or "").replace("\\", "/")
            if "fuseki_client.py" in fname or "call_logger.py" in fname:
                continue
            mod = fname.rsplit("/", 1)[-1].replace(".py", "")
            return f"{mod}.{fr.function}:{fr.lineno}"
    except Exception:
        pass
    return ""
