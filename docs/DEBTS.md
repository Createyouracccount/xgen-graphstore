# DEBTS — 3층(LPG 백엔드) 착수 시 갚을 부채

2층 이관은 동작 보존이 목적이라, Fuseki/RDF/SPARQL 고유 가정이 인터페이스에 일부 남았다.
Neo4j/AGE 백엔드(0.2.0)를 붙일 때 아래를 해소한다. 출처: documents 이관 원장 §4 + B3/B4/B5.

## A. text:query(jena-text/Lucene) 재작성 — 5개 메서드
Neo4j full-text index(`db.index.fulltext.queryNodes`)로 능력은 포팅되나 **쿼리 전면 재작성** 필요.
- `seed_relations_by_fulltext_forward` / `..._reverse` — Lucene 임계 **15**
- `seed_connectivity_relations` — 임계 **80** (양끝 인덱스 매칭)
- `seed_relations_broad` — 임계 **60**
- `seed_classes_by_fulltext` — 임계 **30**, `GROUP_CONCAT` → Cypher `collect()`

**⚠️ 임계값(15/80/60/30)은 Lucene 점수라 LPG에 등가 개념이 없다 → 재보정 필요.**

## B. named graph 전략
RDF named graph(`GRAPH <g>` / `WITH <g>`)는 LPG에 없다. collection별 속성 태깅 또는 multi-database.
- `tag_communities`(DELETE→INSERT), `clear_graph`(CLEAR SILENT), `commit_staged_graph`
  (target/staging/control 3-graph + 멱등 marker), `get_ingest_commit_marker`.
- 병합/rename의 `WITH <g>` 스코프.

## C. 2면 triple 이동 → LPG 노드 병합
- `merge_move_subject`/`merge_move_object` (병합), `rename_move_subject`/`object`/`rename_drop_old_label`.
- LPG는 `apoc.refactor.mergeNodes` 로 의도는 깔끔하나, 현재는 subject/object 양면 트리플 재작성 방식.

## D. OWL/RDFS-as-data 재모델링
스키마가 트리플로 저장·조회·수리됨 → LPG는 라벨/관계타입으로 암묵.
- `get_tbox_schema`, `get_graph_data_for_visualization`, `count_classes`/`count_properties`,
  `clean_subclassof_noise`, `materialize_property_inheritance`.

## E. 기타
- `upload_ttl`(Turtle 문자열) → LPG CSV/`UNWIND`+`MERGE`.
- `triple_exists` ASK 멱등가드 → LPG `MERGE`/`EXISTS` 의미로 재편.
- `raw_query` 성격의 자유쿼리(LLM 노출) — 현재 패키지엔 없음. documents 쪽 프롬프트 계약이 SPARQL↔Cypher로 갈림.
- **gather 통합(정련 부채, 선택)**: documents `_seed_relational`은 정/역방향 2메서드를 호출부에서
  `asyncio.gather(return_exceptions=True)`로 감싼다. 3층에서 "gather를 백엔드 1개 의미연산으로
  합칠지"는 인터페이스 정련 사항 — 지금은 동시성/예외경계를 호출부에 노출(의도).

## F. documents-side 잔류 (패키지로 안 옮긴 것) — 3층 처리 대상 10곳
2층에서 **의도적으로 범위 제외**한 일회용 OWL-as-data. xgen-graphstore로 이관하지 않고
documents `service/ontology/` 에 도메인 로직으로 잔류. LPG 모델 확정 후 처리.
- `post_build_fixer.py` **8곳**: 품질 검증 COUNT(orphan-class·dangling·ungoverned-predicate·
  domain-violation·grounding 등 `FILTER NOT EXISTS` 스칼라).
- `controller/ontology/endpoints/graph_rag_operations.py` **2곳**: `no_label`/`no_type` 인스턴스 COUNT.

이유: 각 쿼리가 단일 호출 + OWL-as-data라 메서드로 올리면 (a)단일사용 추상화(문서 CLAUDE.md 위반)
(b)LPG 미이식 계약 부채. 3층에서 D(OWL-as-data 재모델링)와 함께 일괄 결정.
