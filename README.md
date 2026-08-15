# xgen-graphstore

백엔드 중립 **온톨로지 그래프 저장소** — `OntologyStore` 인터페이스 + Fuseki 구현.
XGEN Python 서비스가 라이브러리로 의존하는 **공유 커널**(HTTP 서비스 아님 — [ADR-001](docs/ADR-001-shared-kernel.md)).

- 코어 의존: `httpx` 만. 호출 계측(call-logging)은 주입식 no-op 기본(`set_call_timer`).
- 목적: RDF(Fuseki) ↔ LPG(Neo4j/AGE) 저장소를 **빌더/구현만 갈아끼워** 교체 가능하게.

## Provenance (이력 단절 보상)

`xgen-documents`의 다음 모듈에서 이관:
- `service/ontology/fuseki_client.py` → `transport.py` (call_logger 의존만 주입식 no-op로 치환)
- `service/ontology/fuseki_queries.py` → `queries.py` (무변경)
- `service/ontology/fuseki_backend.py` → `backend.py` (import 경로만)
- `service/ontology/ontology_store.py` → `store.py` (무변경)

출처 커밋: **`8a81e23`** (documents `feature/ontology-store-b1`, 2층 이관 완료 시점).
이관 경위·게이트·심판 전문: `company/xgen-levelup/docs/온톨로지_저장소_추상화_이관_원장_2026_08_15.md`.

## 사용

```python
from xgen_graphstore import create_store

store = create_store({"backend": "fuseki"})   # 설정 생략 시 env(FUSEKI_URL 등)
counts = await store.class_instance_counts(graph_iri)
```

CLI (기존 메서드 표면 노출만):

```
graphstore health
graphstore ensure-dataset
graphstore count <graph-iri>
graphstore snapshot-hash <graph-iri>     # 정렬 (s,p,o) sha256 — 마이그레이션 전후 등가 확인
```

## 계약표 — 메서드 × R/W × LPG 이식 난이도

`T`=TRIVIAL · `M`=MODERATE · `H`=HARD (LPG/Cypher 재작성 난이도). H 상세는 [DEBTS.md](docs/DEBTS.md).

| 메서드 | R/W | LPG | 비고 |
|---|---|---|---|
| `node_properties` | R | T | 노드 datatype 속성 |
| `property_values` | R | T/M | 속성=노드키 매핑 |
| `neighbors` | R | M | 1홉 이웃 |
| `triple_exists` | R | T/M | ASK → LPG는 MERGE/EXISTS |
| `count_node_triples` | R | T | subject∪object |
| `class_instance_counts` | R | M | GROUP BY, owl:Class 관례 |
| `relation_triple_counts` | R | M | 인스턴스간 술어 집계 |
| `community_edges` / `community_labels` | R | M | 간선/라벨 스캔 |
| `seed_chunk_relations` | R | M | VALUES 바인딩(→파라미터, 부채 아님) |
| `predicate_labels` | R | M | 순수 SPARQL |
| `seed_relations_by_fulltext_forward`/`reverse` | R | **H** | text:query(jena-text), 임계 15 |
| `seed_connectivity_relations` | R | **H** | text:query, 임계 80 |
| `seed_relations_broad` | R | **H** | text:query, 임계 60 |
| `seed_classes_by_fulltext` | R | **H** | text:query, 임계 30, GROUP_CONCAT |
| `insert_data` / `delete_data` | W | M | ASK 멱등가드는 호출부 |
| `delete_node_subject_side`/`object_side` | W | T/M | 양면 DELETE(LPG=DETACH DELETE) |
| `tag_communities` | W | M | DELETE→INSERT, named graph |
| `merge_move_subject`/`object` | W | **H** | 2면 triple 이동(LPG=apoc.mergeNodes) |
| `merge_normalized_instances_labels` / `same_label_nodes` | R | M | ko-label SELECT |
| `rename_move_subject`/`object`/`rename_drop_old_label` | W | **H** | 라벨기준 3단계 이동 |
| `upload_ttl` | W | **H** | Turtle(LPG=CSV/UNWIND) |
| `commit_staged_graph` / `get_ingest_commit_marker` | W/R | **H** | named graph staging/control |
| `clear_graph` | W | M | CLEAR SILENT(named graph) |
| `clean_subclassof_noise` / `materialize_property_inheritance` | W | **H** | OWL/RDFS-as-data |
| `get_tbox_schema` / `get_graph_data_for_visualization` | R | **H** | OWL/RDFS 스키마-as-data |
| `count_classes` / `count_properties` / `get_triple_count` | R | T/M | |
| `health_check` / `ensure_dataset` | R/W | M | admin |

## 테스트

```
pytest -m "not live"    # CI 기본 — 봉인 골든 + 목-transport 파싱 등가
pytest -m live          # 실제 Fuseki 필요(FUSEKI_URL). B4/B5 왕복 스모크
```

## 로드맵

- **0.1.0** (현재): 팩토리 + Fuseki 백엔드 + CLI.
- **0.2.0**: Neo4j 백엔드 = **3층 착수**(DEBTS.md의 H 항목 재작성).
- **0.3.0**: 라우터(dual-write·테넌트 분기) — 두 번째 백엔드 위에서만([ADR-002](docs/ADR-002-router-deferred.md)).

## ⚠️ 머지 게이트 (이월된 라이브 스모크)

이 리포와 xgen-documents 는 **Fuseki 환경(docker/CI)에서 `pytest -m live` 통과 전 develop 머지 금지.**
2층 이관의 B4 write 왕복·B5 병합 왕복(병합 후 구 URI 잔존 0)이 로컬 docker 부재로 이번 세션에
미검증 이월됨. 목-transport + git-앵커드 골든은 통과했으나 실 store e2e 는 관문으로 남긴다.
