# xgen-graphstore

Backend-neutral **ontology graph store** — the `OntologyStore` seam plus a Fuseki (RDF/SPARQL)
implementation and a Neo4j (LPG/Cypher) **PoC** implementation.
A **shared kernel** that XGEN Python services depend on as a library (not an HTTP service — see [ADR-001](docs/ADR-001-shared-kernel.md)).

- Core dependency: `httpx` only. Call instrumentation (call-logging) defaults to an injectable no-op (`set_call_timer`).
- Goal: make the RDF (Fuseki) ↔ LPG (Neo4j/AGE) store **swappable by replacing the builders/implementation only**.
- **Proven**: the same contract runs on Fuseki and Neo4j with byte-identical results by changing one config key — see [Swap proof](#swap-proof-2026-08-16).

## Provenance (history-break compensation)

Extracted from these `xgen-documents` modules:
- `service/ontology/fuseki_client.py` → `transport.py` (only the `call_logger` dependency swapped for an injectable no-op)
- `service/ontology/fuseki_queries.py` → `queries.py` (verbatim + provenance banner comment)
- `service/ontology/fuseki_backend.py` → `backend.py` (import paths repointed only)
- `service/ontology/ontology_store.py` → `store.py` (verbatim)

Source commit: **`8a81e23`** (documents `feature/ontology-store-b1`, at the 2nd-layer migration-complete point).
Full migration history, gates, and adversarial-judge records: `company/xgen-levelup/docs/온톨로지_저장소_추상화_이관_원장_2026_08_15.md`.

## Usage

```python
from xgen_graphstore import create_store

store = create_store({"backend": "fuseki"})   # config omitted → read from env (FUSEKI_URL, etc.)
counts = await store.class_instance_counts(graph_iri)
```

CLI (surfaces existing methods only — no new capability). The `graphstore` console script is
provided after `pip install -e .`; without an install, invoke the module directly:

```
graphstore health                         # or: python -m xgen_graphstore.cli health
graphstore ensure-dataset
graphstore count <graph-iri>
graphstore snapshot-hash <graph-iri>      # sorted (s,p,o) sha256 — verify pre/post-migration equivalence
```

Connection config comes from env (`FUSEKI_URL`, etc.) or `--base-url/--dataset/--username/--password`.

## The contract surface — methods × R/W × LPG portability

`T`=TRIVIAL · `M`=MODERATE · `H`=HARD (LPG/Cypher rewrite difficulty). H details in [DEBTS.md](docs/DEBTS.md).

> **Note on the `OntologyStore` Protocol.** The `store.py` Protocol currently *formally* declares the
> five core read methods (`node_properties`, `property_values`, `neighbors`, `triple_exists`,
> `count_node_triples`); the full contract below is the **`FusekiBackend` method surface** and is
> satisfied structurally (duck-typed). Promoting the Protocol to the enforced full contract — so a
> future `Neo4jBackend` is checked against every method — is tracked as the first task for **0.2.0**
> (see [DEBTS.md §G-Protocol](docs/DEBTS.md)). Until then this table, not the Protocol, is the source of truth.

| Method | R/W | LPG | Notes |
|---|---|---|---|
| `node_properties` | R | T | Node datatype properties |
| `property_values` | R | T/M | Property → node-key mapping |
| `neighbors` | R | M | One-hop neighbors |
| `triple_exists` | R | T/M | ASK → LPG uses MERGE/EXISTS |
| `count_node_triples` | R | T | subject ∪ object |
| `class_instance_counts` | R | M | GROUP BY, owl:Class convention |
| `relation_triple_counts` | R | M | Predicate aggregation between instances |
| `community_edges` / `community_labels` | R | M | Edge/label scan |
| `seed_chunk_relations` | R | M | VALUES binding (→ parameters, not a debt) |
| `predicate_labels` | R | M | Pure SPARQL |
| `seed_relations_by_fulltext_forward`/`reverse` | R | **H** | text:query (jena-text), threshold 15 |
| `seed_connectivity_relations` | R | **H** | text:query, threshold 80 |
| `seed_relations_broad` | R | **H** | text:query, threshold 60 |
| `seed_classes_by_fulltext` | R | **H** | text:query, threshold 30, GROUP_CONCAT |
| `insert_data` / `delete_data` | W | M | ASK idempotency guard lives at the call site |
| `delete_node_subject_side`/`object_side` | W | T/M | Two-sided DELETE (LPG = DETACH DELETE) |
| `tag_communities` | W | M | DELETE→INSERT, named graph |
| `merge_move_subject`/`object` | W | **H** | Two-sided triple move (LPG = apoc.mergeNodes) |
| `merge_normalized_instances_labels` / `same_label_nodes` | R | M | ko-label SELECT |
| `rename_move_subject`/`object`/`rename_drop_old_label` | W | **H** | Label-based three-step move |
| `upload_ttl` | W | **H** | Turtle (LPG = CSV/UNWIND) |
| `commit_staged_graph` / `get_ingest_commit_marker` | W/R | **H** | Named-graph staging/control |
| `clear_graph` | W | M | CLEAR SILENT (named graph) |
| `clean_subclassof_noise` / `materialize_property_inheritance` | W | **H** | OWL/RDFS-as-data |
| `get_tbox_schema` / `get_graph_data_for_visualization` | R | **H** | OWL/RDFS schema-as-data |
| `count_classes` / `count_properties` / `get_triple_count` | R | T/M | |
| `health_check` / `ensure_dataset` | R/W | M | Admin |
| `sparql_query` / `sparql_update` | R/W | — | Raw transport escape hatch (kept from the client) |

## Tests

```
pytest -m "not live"    # CI default — sealed golden + mock-transport parse-equivalence (9 tests)
pytest -m live          # requires real servers. Fuseki smoke (FUSEKI_URL) + cross-backend swap
                        # proof (also needs Neo4j: NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD)
```

Verified 2026-08-16 against Fuseki 5.1.0 + Neo4j 5: mock 9/9, Fuseki live 2/2,
cross-backend swap 2/2, and the CLI (`health`/`count`/`snapshot-hash`, deterministic) end-to-end.

## Swap proof (2026-08-16)

`tests/test_cross_backend_swap.py` runs the **same operations** through `create_store({"backend": X})`
for `X ∈ {fuseki, neo4j}` and asserts the observable traces are **identical** across the two engines
(SPARQL triplestore vs Cypher LPG). Verified against Fuseki 5.1.0 + Neo4j 5:

| Roundtrip | Trace (both backends) |
|---|---|
| B4 write (`insert → triple_exists → count → delete → triple_exists → count`) | `[True, 1, False, 0]` |
| B5 merge (2-sided move: `count → merge_move_subject → merge_move_object → count(old) → count(canonical)`) | `[2, 0, 2]` |

The swap point is **one place** — the `create_store` factory. `xgen-documents` calls `create_store()`
and never names a backend; switching is one env var (`GRAPHSTORE_BACKEND`). This is the concrete
evidence that the "big work" (Cypher vs SPARQL) lives entirely inside graphstore, not in the callers.

> **PoC scope (honest).** `Neo4jBackend` currently implements the 6 resource-triple core methods
> (`insert_data`/`delete_data`/`triple_exists`/`count_node_triples`/`merge_move_subject`/`merge_move_object`)
> + `health_check`. The rest raise `NotImplementedError` pointing at the DEBTS **H** items (text:query
> full-text, OWL-as-data, named-graph staging, literal properties, TTL) — the genuine 3rd-layer
> remodeling. Those are all inside this package too.

## Roadmap

- **0.1.0** (current): factory + Fuseki backend + CLI + **Neo4j swap PoC** (6 core methods, cross-backend proof).
- **0.2.0**: complete the Neo4j backend = **3rd layer** (rewrite the remaining H items in DEBTS.md).
  Also promote `OntologyStore` to the enforced full contract (DEBTS §G-Protocol).
- **0.3.0**: router (dual-write / tenant routing) — only on top of a second backend ([ADR-002](docs/ADR-002-router-deferred.md)).

## Merge gate (live smoke — cleared 2026-08-16)

~~Deferred live smoke~~ **cleared.** `pytest -m live` passes 2/2 against a real Fuseki (docker, jena 5.1.0):
- **B4 write roundtrip**: insert → ASK True → delete → ASK False ✓
- **B5 merge roundtrip**: after two-sided move (subject + object), old URI residue **0** ✓

Verified inside a throwaway dataset (`graphstore_smoke`), then dropped — the live `xgen` dataset was untouched.
On top of the mock-transport + git-anchored golden tests, the real store e2e now passes too.

**Remaining pre-merge decision:** dependency-pin conversion (local path → git pin) + platform split
resolution — see [DEBTS.md §G](docs/DEBTS.md). Remote: `github.com/Createyouracccount/xgen-graphstore` (private).
