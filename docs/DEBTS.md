# DEBTS — 3층(LPG 백엔드) 착수 시 갚을 부채

2층 이관은 동작 보존이 목적이라, Fuseki/RDF/SPARQL 고유 가정이 인터페이스에 일부 남았다.
Neo4j/AGE 백엔드(0.2.0)를 붙일 때 아래를 해소한다. 출처: documents 이관 원장 §4 + B3/B4/B5.

> **PoC 상태(2026-08-16)**: `neo4j_backend.py` 가 리소스-트리플 핵심 6메서드
> (insert/delete/triple_exists/count_node_triples/merge_move_subject/merge_move_object)를
> Cypher 로 구현해 **Fuseki 와 교차 스왑 증명 통과**(`test_cross_backend_swap.py`).
> 아래 A~F 는 그 PoC 가 `NotImplementedError` 로 남긴 **진짜 3층 재모델링** 대상이다.

## A. text:query(jena-text/Lucene) 재작성 — 5개 메서드
Neo4j full-text index(`db.index.fulltext.queryNodes`)로 능력은 포팅되나 **쿼리 전면 재작성** 필요.
- `seed_relations_by_fulltext_forward` / `..._reverse` — Lucene 임계 **15**
- `seed_connectivity_relations` — 임계 **80** (양끝 인덱스 매칭)
- `seed_relations_broad` — 임계 **60**
- `seed_classes_by_fulltext` — 임계 **30**, `GROUP_CONCAT` → Cypher `collect()`

**~~⚠️ 임계값(15/80/60/30)은 Lucene 점수라 LPG에 등가 개념이 없다 → 재보정 필요.~~**

### ⭐ A-정정 (2026-08-23): 위 진단은 틀렸다 — 임계값은 점수가 아니라 개수다
`(?s ?sc) text:query (rdfs:label "terms" 15)` 의 세 번째 인자는 jena-text 문법상
**상위 N개 제한(limit)** 이다. Lucene 점수 임계가 아니다.
→ **"재보정" 이 아니라 "같은 상위 N 개를 뽑으면 된다"** — 등가 이식이 가능하다.

**이식 완료(Neo4j, 2026-08-23)**: `GRAPH_SEARCH` 2/7 → **7/7**
- `ensure_fulltext_index()` — Lucene full-text index, **analyzer=cjk** 로 Fuseki 의 CJKAnalyzer 와 정렬.
  `db.awaitIndex` 로 온라인 대기(직후 질의 시 빈 결과가 나는 무증상 위험 차단).
- `_ft_nodes(terms, N)` — `text:query` 등가. Lucene 특수문자 이스케이프(입력이 질의 문법으로 해석되는 것 차단).
- 5종: connectivity(양끝 상위80)·broad(단끝 상위60)·forward/reverse(상위15+술어핀)·classes(상위30+owl:Class+집계).

**실측 파리티**(정본 `eval_runs/graphdb_selection/search_port_parity_result.txt`):
- 시드 선택 자체는 **98~100% 일치** — analyzer 를 맞추면 fulltext 는 사실상 등가.
- `seed_connectivity_relations` recall **100%**, `broad` **89%**, forward/reverse 비포화 **95~100%**.

**⚠️ 남는 근본 한계 — 동점 절단의 비결정성**: `broad` 의 잔여 차이는 이식 결함이 아니라
**Lucene 점수 동점** 탓이다. 실측('대학교' 상위60): 점수 2.864 가 **47개 동점**.
동점자가 자를 자리보다 많으면 "어느 47개를 뽑나" 가 엔진마다 갈린다.
같은 analyzer·같은 알고리즘을 써도 **tie-break 규칙까지 같을 수는 없다** — 이식으로 못 없앤다.
완전 결정화하려면 점수 외 2차 정렬키(uri 등)를 양쪽에 강제해야 하는데, 그건 원본 동작을
바꾸는 것이라 **별도 결정**이 필요하다.

**⚠️ 측정 함정**: LIMIT 절단이 파리티를 망친다. 같은 질의가 LIMIT 100 → recall 13%,
LIMIT 해제 → **99%**. `ORDER BY` 없는 LIMIT 은 순서를 보장하지 않아 두 엔진이 서로 다른
100건을 자른다. 파리티 비교는 넉넉한 LIMIT 으로 하고, 하드코딩 LIMIT(forward/reverse 의 40)은
'포화' 로 표시해 **이식 결함과 절단 인공물을 구분**해야 한다.

### A-실측 (2026-08-21) — 이건 "부채"가 아니라 **검색 기능의 생사**다
실데이터 3백엔드 실측 결과(정본 `docs/그래프DB_선정_2026_08_17.md` §15):
- 검색 경로(`multi_turn_rag.query`)가 부르는 store 메서드는 **7개**이고, LPG 백엔드는 **0/7** 보유.
  위 5개(fulltext) + `predicate_labels` + `seed_chunk_relations`(NotImplemented).
- **호출부가 전부 `except Exception` 으로 흡수** → 예외도 로그도 없이 빈 그래프 근거로 degrade.
  답변은 정상처럼 보이나 실제론 벡터검색만 동작. **"회색지대 기본값 금지" 정면 위반.**
- **1차 차단 완료(2026-08-21)**: `capabilities.Workload` + `probe_workload/require_workload/preflight_report`
  로 **부팅 시점 명시적 차단** 제공. 런타임 흡수는 graphstore가 막을 수 없으므로 부팅에서 드러낸다.
  → 이제 무증상은 아니다. **단 검색 자체는 여전히 불가** — 아래 순서로 갚아야 산다.

### A-착수 순서 (검색을 LPG에서 살리는 최소 경로)
1. **`seed_chunk_relations` 먼저** — fulltext 무관(VALUES 바인딩)이라 **순수 Cypher로 바로 이식 가능**.
   3백엔드 공정 비교의 기준선(control)이자, HippoRAG 1홉 확장의 유일 축. **난이도 최저·가치 즉시.**
2. **`predicate_labels`** — 순수 SPARQL이라 Cypher 등가 직역 가능. fulltext 게이트의 전제.
3. **LPG 매핑 승격(선행 권장)** — 현재 `-[:REL {p:"..."}]->` 라 술어가 property.
   `-[:coOccursWith]->` 로 **관계타입 승격**해야 관계타입 인덱스를 타고, fulltext 이식 성능도 정상화(§15.3).
4. **fulltext 5종** — Neo4j `db.index.fulltext.queryNodes` (Arcade는 별도 검토).
   임계값은 이식이 아니라 **재보정**: 동일 질의셋으로 Fuseki 결과와 recall/precision 맞추는 튜닝 필요.
5. **회귀 게이트**: 이식 후 `require_workload(store, Workload.GRAPH_SEARCH)` 가 통과해야 하고,
   실질 검증은 `triples_used>0` / `evidence_nodes>0` 로 판정(answer 유무는 신호가 아님).

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

## D-2. `graph_browse` 는 이식 과제가 아니다 — LPG 리터럴 모델에 막혀 있다 (0824 실측)

프리플라이트가 `graph_browse` 를 LPG **0/7** 로 보고한다. 그중 `node_properties`·
`property_values`·`neighbors` 는 `OntologyStore` **Protocol 이 선언한 메서드**라, 패키지가
자기 계약을 못 지키는 상태다. 그런데 원인을 파보니 **Cypher 를 못 써서가 아니다.**

`ntriples.py` 의 리터럴 이관이 정보를 두 가지 버린다:

| 버리는 것 | 근거 |
|---|---|
| **술어 네임스페이스** | `parse_literals`: `key = localname(p)` — `<…#설립연도>` 가 `설립연도` 로만 남는다 |
| **언어 태그** | `_LITERAL_RE` 가 `(?:@[\w-]+\|\^\^<[^>]+>)?` 를 **비캡처**로 흘린다 |

그 결과:

1. **`node_properties` 는 `property_uri` 를 정직하게 낼 수 없다.** 리터럴 속성의 원 URI 가
   사라졌다. `NS_DOMAIN + key` 로 되살리는 건 **네임스페이스를 추측하는 것**이라
   프로젝트 원칙("회색지대 기본값 금지")에 정면으로 어긋난다. 서로 다른 네임스페이스의
   같은 localname 은 LPG 에서 이미 한 칸에 뭉쳐 있다.
2. **세 메서드 전부 `FILTER(LANG(?x) = "ko" || LANG(?x) = "")` 를 재현할 수 없다.**
   원본 browse 질의는 전부 이 필터를 쓴다. LPG 에는 언어 정보가 없어 ko/en 라벨이
   구분 없이 리스트에 섞여 있다(실측: `coOccursWith` 라벨이 "함께언급"·"co-occurs with" 둘 다).
   필터 없이 내면 화면에 영어 라벨이 섞여 나오는데, **조용히 다른 결과**다.

→ **선행 과제는 리터럴 모델 확장**이다(술어 URI 보존 + 언어 태그 보존). 그게 되기 전에
`graph_browse` 를 "이식"하면 추측값과 언어 혼입을 제품에 넣는 것이다. 지금은
`require_workload(store, Workload.GRAPH_BROWSE)` 가 **명시적으로 차단**하는 편이 옳다.

### D-2 진행 — ① 파서 (2026-08-27, 완료) / ② 저장 모델 (미착수)

손실이 두 층에 있어 둘로 나눴다. **①만으로는 `graph_browse` 가 열리지 않는다.**

**① 파싱 계층 — 더는 버리지 않는다.** `ntriples.py` 한 곳만 고쳐 두 백엔드를 동시에 덮는다
(이 파일이 백엔드 공통 파싱 단일지점인 이유 그대로).
- `_LITERAL_RE` 의 `(?:@[\w-]+|\^\^<[^>]+>)?` 를 **캡처로 전환** → `lang`·`dtype` 보존
- `parse_literals` 가 `p`(술어 URI 원본)를 함께 반환 — `key` 는 localname 이라 **손실 축약**임을
  독스트링에 명기. `NS_DOMAIN + key` 되살리기는 네임스페이스 추측이라 금지
- `group_literals_by_key` 도 통과시킨다 — 파서가 보존한 것을 한 층 아래서 다시 버리면 무의미
- **필드 추가만.** 기존 소비자(neo4j `r.v`/`r.s`, arcade `r["v"]`/`r["s"]`)는 무변경 동작

**실그래프 대조 검증**(`ui_news100` 18,818 트리플을 N-Triples 로 내려받아 파싱, 합성 아님):

| 항목 | 결과 |
|---|---|
| 커버리지 | 18,818줄 → 리소스 6,321 + 리터럴 12,497 = **18,818, 미분류 0** |
| 언어태그 분포 | `''` 8,040 · `ko` 4,224 · `en` 233 — **Fuseki SPARQL 정답과 3값 전부 일치** |
| localname 충돌 | **0** — 이 그래프는 네임스페이스가 단일이다 |
| 같은 (s,key) 다국어 | **1건** — `coOccursWith` label = ("함께언급"@ko, "co-occurs with"@en) |

정직한 해석 두 가지:
- **술어 URI 보존은 이 그래프의 현존 오류를 고치는 게 아니다.** 충돌 0이므로 지금은 계약 정합
  (`node_properties` 가 `?p` 를 추측 없이 낼 수 있게 하는 것)이 이득의 전부다. 다중 네임스페이스
  그래프가 들어오면 그때 오류 방지로 바뀐다.
- **언어태그는 지금 당장 필요하다.** label 보유 주어 4,456 중 **232(5.2%)가 en 라벨만** 갖는다
  (대부분 술어 라벨: `org:founded`·`sourceChunk`). 원본 `FILTER(LANG="ko"||"")` 는 이들에게
  라벨을 주지 않는데, 언어 정보가 없는 LPG 는 **그 선택 자체를 할 수 없다.**

**② 저장 모델 — 미착수, 설계 결정 필요.** 백엔드는 여전히 `n.<localname> = [값...]` 만 쓴다
(neo4j_backend.py `insert_data`, arcade_backend.py `insert_data`). 파서가 주는 `p`/`lang` 은
현재 **버려진다**. 여기를 고치는 것은:
- **재적재 비가역** — 이미 적재된 LPG 그래프는 언어 정보를 복구할 수 없다
- **검색 계약과 충돌 가능** — fulltext 인덱스가 `Resource.label` 배열을 대상으로 하므로,
  값 배열의 내용이 바뀌면 `GRAPH_SEARCH 8/8`·파리티가 흔들릴 수 있다
- **검증에 라이브 백엔드 필요** — `gs-neo4j`·`gs-arcade` 가 `Exited(137)` 인 상태로
  Cypher 를 쓰면 검증 없는 저장 모델을 확정하는 것이다

⚠️ 이 손실은 browse 에만 국한되지 않는다 — 이미 적재된 LPG 그래프는 언어 정보를 **복구할 수
없다.** 모델을 고치면 **재적재가 필요하다.**

## E. 기타
- `upload_ttl`(Turtle 문자열) → LPG CSV/`UNWIND`+`MERGE`.
- `triple_exists` ASK 멱등가드 → LPG `MERGE`/`EXISTS` 의미로 재편.
- `raw_query` 성격의 자유쿼리(LLM 노출) — 현재 패키지엔 없음. documents 쪽 프롬프트 계약이 SPARQL↔Cypher로 갈림.
- **gather 통합(정련 부채, 선택)**: documents `_seed_relational`은 정/역방향 2메서드를 호출부에서
  `asyncio.gather(return_exceptions=True)`로 감싼다. 3층에서 "gather를 백엔드 1개 의미연산으로
  합칠지"는 인터페이스 정련 사항 — 지금은 동시성/예외경계를 호출부에 노출(의도).

## E-2. 적재 성능 — 인덱스가 본진, 벌크 도구는 그 다음

**⚠️ 122배 격차의 정체는 '엔진 성능' 이 아니라 '인덱스 부재' 였다.**
같은 데이터(클래스구조 17,094줄)를 fuseki 5.9s · arcade 4.8s 로 넣는데 Neo4j 는 588.6s 였다.
인덱스 현황을 보니 ArcadeBackend 는 `_ensure_schema` 에서 `Resource[uri] UNIQUE` 를 만드는데
**Neo4jBackend 에는 uri 인덱스가 아예 없었다** → `MERGE (n:Resource {uri:...})` 가 매번 풀스캔.

→ **수정(2026-08-23)**: `Neo4jBackend.ensure_schema()` 로 `CREATE CONSTRAINT ... REQUIRE n.uri IS UNIQUE`
를 보장하고, `insert_data` 첫 호출에서 1회 실행(`_ensure_schema_once`). 실측 A/B 는
`eval_runs/graphdb_selection/measure_bulk_load.py` / `bulk_load_result.txt`.

**교훈**: 백엔드 간 성능 비교 전에 **각 백엔드가 동등한 인덱스를 갖췄는지 먼저 확인**할 것.
안 그러면 구현 결함을 엔진 특성으로 오독한다.

### 남은 벌크 경로 (초기 대량 적재용, 미구현)
| 경로 | 성격 | 제약 |
|---|---|---|
| `neo4j-admin database import` | 오프라인 벌크(가장 빠름) | **DB 정지 + CSV 변환 + 기존 데이터 삭제** 필요 → 최초 1회 적재 전용. 증분 불가 |
| `apoc.periodic.iterate` | 온라인 배치·트랜잭션 분할 | **APOC 플러그인 필요** — 현재 컨테이너에 미설치(프로시저 0개) |
| 현행 `UNWIND` + 인덱스 | 온라인 증분 | 인덱스만 있으면 실용 범위. **기본 경로로 유지** |

우선순위: 인덱스(완료) → 필요 시 APOC → 초기 마이그레이션에만 neo4j-admin.

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

---

## G. 추출 신선도 — 패키지가 정본보다 낡는다 (2026-08-24 실측)

**사고**: `74a322a` 는 `fuseki_client.py` 를 `8a81e23` 시점에서 추출했는데, 정본
계보(`xgen-documents` `ontology-search`, develop 대비 +52)는 이미 그 앞을 지나 있었다.
추출본은 **실측 채택된 수리 4건을 조용히 되돌렸다**:

| 잃은 수리 | 근거 |
|---|---|
| `compact_dataset` / `has_active_compact` | TDB2 copy-on-write 팽창(1.4GB→121GB, 0715) + 0814 배타대기 사고 |
| `sparql_query_csv` | 44만 행 JSON bindings 로 컨테이너 OOM |
| `sparql_query` 로그 상한(50행) | 40.8만 행 덤프로 도커 로그 179MB |
| `get_graph_data_for_visualization` 성능 | owl:Class 조인 제거 83s→0.0s, ObjectProperty 한정 16s→0.5s |

**왜 안 보이나**: 메서드 이름은 전부 있어서 import 도 되고 테스트도 통과한다.
`FusekiClient` → `FusekiBackend` 스왑 시 `sparql_query_csv` 만 AttributeError 로
터지고(pipeline.py 10곳), 나머지 3건은 **아무 신호 없이 성능·안정성만 되돌아간다.**

→ `b0f8c2a` 에서 4건 전량 전방이식(본문 바이트 동일).

### 질의 빌더 낡음 — 해소 (`6ce3c74`)

`queries.py` 도 develop 계보에서 추출됐다. 함수 단위 실측으로 30개 중 7개가 지목됐고,
전수 대조 결과 **실제로 틀린 것은 3개 + 아예 없던 것 2개**였다(나머지 4개는 함수 단위
게이트의 거짓양성 — 바이트 동일 확인).

| 빌더 | 무엇이 없었나 |
|---|---|
| `seed_chunk_relations_query` | `FILTER(?p != :coOccursWith)`. 정본은 정밀 SVO 와 동시출현 약관계를 **슬롯 분리**한다 — 단일 정렬 LIMIT 는 SVO 가 항상 선점해 co-occ 가 0 이 되기 때문(mixed20k 실측) |
| `seed_chunk_cooccurrence_query` | **빌더 자체가 없음.** 약관계 슬롯. 술어가 단일이라 `?pLabel` 미조회(ko/en 2행 중복으로 슬롯 절반 낭비 방지) |
| `seed_connectivity_relations_query` | 동일 필터. ⚠️ `seed_relations_broad` 에는 **일부러 안 건다** — recall 폴백이자 SVO 희소 구간의 유일한 관계원 |
| `seed_classes_by_fulltext_query` | `owl:equivalentClass` 동치폐포(R9, 국가 11→16) + `rdfs:subClassOf*` 이행폐포 + `?directs`(150캡에서 폐포가 직접 인스턴스를 밀어내지 않도록 주입순서 고정) + `ONTOLOGY_CLASS_SEED` 모드 |
| `merge_journal_insert_update` | **빌더 자체가 없음.** 물리 병합이 비가역이라 `__id_journal` 에 `(canonical, mergedFrom, old)` 를 남긴다. **실서버 Fuseki 에 해당 그래프 실재 확인** |

5건 전량 정본 재구성과 **바이트 동일** 확인 후 `test_golden_sealed.py` 에 봉인.

**LPG 도 같은 결함이었다** — 두 백엔드의 청크 시드가 *낡은 Fuseki 질의의 이식본*이라
동시출현 엣지를 정밀 슬롯에 섞고 있었고 분리 슬롯이 없었다. 양쪽 수정 →
`GRAPH_SEARCH` **3백엔드 전부 8/8**.

### 게이트 — 이식 후에도 쓸 수 있게

`eval_runs/graphdb_selection/extraction_freshness.py` (exit 1 = 낡음).
빌더별 **대조 기준점**(`extraction_baseline.json`)을 두어, 대조를 마친 빌더는 그 커밋
이후의 변경만 센다. 안 그러면 이식 후에도 영구 적색이라 아무도 안 본다.
현재 **신선 33 / 낡음 0 / 판정불가 0**.
음성 대조 2회로 자명한 초록이 아님을 확인했다(기준점 제거 · 기준점 30커밋 후퇴 → 둘 다 적색).

### 규범 — 재추출은 머지 순서에 종속된다
`ontology-search`(+52) 와 `feature/ontology-store-b1`(+9) 은 서로를 모르는 미배포 2브랜치다.
**`ontology-search` 를 먼저 develop 에 넣고, 그 위에서 seam 을 다시 뜬다.** 반대로 하면
b1 의 추출본이 정본을 덮는다. 머지 전 게이트 통과(exit 0) 를 관문으로 둔다.

### 남은 것
- `graph_browse` LPG **0/7** — `node_properties`·`property_values`·`neighbors` 는
  `OntologyStore` **Protocol 선언 메서드인데 LPG 미구현**이다(패키지가 자기 계약 위반).
- `ontology_build` neo4j 8/16 · arcade 2/16.
- 이식 후 Neo4j·Arcade **파리티 재측정** — 기존 98~100% 는 낡은 기준선에 대한 값이다.
