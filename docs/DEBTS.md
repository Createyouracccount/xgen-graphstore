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

## G. 의존 핀 전략 — develop 머지 전 필수 결정 (플랫폼 단절)
이 패키지는 **private GitHub**(`github.com/Createyouracccount/xgen-graphstore`)에 있고,
documents 는 **GitLab**(`gitlab.x2bee.com/xgen2.0`)에 산다. documents 는 개발 중
`[tool.uv.sources]` **로컬경로 editable** 로 이 패키지를 참조한다(dev 전용, 그대로 유지).

**GitLab CI 는 private GitHub 리포를 기본으로 끌어오지 못한다.** develop 머지 시점에 로컬경로 →
git/버전 핀으로 전환해야 하며, 그 전에 아래 중 하나를 결정한다:
- **(a) GitLab 미러 remote 추가 후 git 핀이 미러를 가리킴 (권고)** — 플랫폼 단절 해소, CI 자격증명 불필요.
- (b) GitLab CI 에 GitHub 자격증명(deploy token 등) 배선 후 git 핀이 GitHub 를 직접 가리킴.

**핀 전환·미러/자격증명 결정 전 develop 머지 금지.** (공개 전환은 별도 인간 승인 사안 — 현재 private.)

## G-Protocol. OntologyStore 인터페이스 승격 (0.2.0 첫 과제)
현재 `store.py` 의 `OntologyStore` Protocol 은 **핵심 read 5개만 정식 선언**한다
(`node_properties`·`property_values`·`neighbors`·`triple_exists`·`count_node_triples`).
나머지 계약(FusekiBackend 자체 의미 메서드 28 + transport 상속 19)은 **구조적(duck-typed)으로만** 만족된다.

- 2층 이관 시점엔 documents 가 `FusekiBackend` 를 직접 참조(타입 힌트로 Protocol 을 안 씀)이라 무해했다.
- 그러나 **3층(Neo4jBackend) 착수 전엔 Protocol 이 전체 계약을 강제해야 한다** — 그래야 두 번째 백엔드가
  누락 메서드 없이 컴파일/타입체크 단계에서 걸린다. 이게 이 리포의 존재 이유(백엔드 스왑)의 척추다.
- 과제: FusekiBackend 표면(계약표)의 시그니처를 Protocol 로 승격. 시그니처는 백엔드에서 정확히 미러링.
  `sparql_query`/`sparql_update` 같은 raw escape 는 Protocol 에 넣을지(백엔드 중립성 훼손) 별도 판단.
