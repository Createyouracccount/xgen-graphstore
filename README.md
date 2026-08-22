# xgen-graphstore

Backend-neutral **ontology graph-store router** — the `OntologyStore` seam, a pluggable backend
**registry**, and a **capability contract**, with a Fuseki (RDF/SPARQL) implementation and a
Neo4j (LPG/Cypher) **PoC** implementation.
A **shared kernel** that XGEN Python services depend on as a library (not an HTTP service — see [ADR-001](docs/ADR-001-shared-kernel.md)).

- Core dependency: `httpx` only. Call instrumentation (call-logging) defaults to an injectable no-op (`set_call_timer`).
- Goal: **any DB plugs in and XGEN works with no silent breakage** — swap the store by replacing the implementation only.
- **Proven**: the same contract runs on Fuseki and Neo4j with byte-identical results by changing one config key — see [Swap proof](#swap-proof-2026-08-16).
- **Router (staged)**: registry + capability contract now (R1); capability-based multi-backend routing next (R2); dual-write/tenant later (R3) — see [ADR-003](docs/ADR-003-router-staged.md).

## Router — register any DB, fail clearly (R1)

```python
from xgen_graphstore import create_store, register_backend, available_backends
from xgen_graphstore import Capability, supports, require_capability

register_backend("mydb", lambda cfg: MyBackend(**cfg))   # any DB plugs in — no core edit
store = create_store({"backend": "mydb"})                # or env GRAPHSTORE_BACKEND; default "fuseki"

supports(store, Capability.FULLTEXT_SEARCH)   # introspect before calling
require_capability(store, Capability.NAMED_GRAPH)   # -> CapabilityError if unsupported (never silent)
```

Each backend declares `CAPABILITIES`. Unsupported operations raise `CapabilityError` (naming the
backend + capability + DEBTS reference) instead of silently misbehaving — aligned with the project's
"no gray-area defaults" rule. The Neo4j PoC declares only `CORE_TRIPLE_RW`; calling a full-text or
OWL-schema method on it is a clear `CapabilityError`, not a wrong-but-quiet result.

> **What the router does and does not do.** It selects the right backend and makes capability gaps
> explicit. It does **not** complete a half-implemented backend — full functionality on a given DB
> still requires that backend to implement the contract (DEBTS **H** items). "Works with no problems"
> = clean selection + loud gaps, not "every DB is magically complete."

## Preflight — block silent degradation at boot

A capability gap only helps if somebody sees it. Callers in `xgen-documents` wrap graph-search calls in
`except Exception`, so a `CapabilityError` raised at request time is **swallowed**: the answer still comes
back, but with zero graph evidence — vector search only. Measured on real data: the LPG backends supply
**0 of the 7** methods the search path calls, entirely silently.

The library cannot stop a caller from swallowing exceptions, so it surfaces the gap **at boot** instead:

```python
from xgen_graphstore import create_store, Workload, preflight_report, require_workload

store = create_store()                                  # env GRAPHSTORE_BACKEND
print(preflight_report(store))                          # log which workloads this backend can serve
require_workload(store, Workload.GRAPH_SEARCH)          # -> CapabilityError if search would be silently empty
```

`Workload` groups the methods a real task actually calls — `CORE_CRUD`, `GRAPH_SEARCH`, `GRAPH_ALGO` —
so the check reflects the workload, not one method at a time. Current measured coverage:

| backend | CORE_CRUD | GRAPH_SEARCH | GRAPH_ALGO |
|---|---|---|---|
| fuseki | 6/6 | **7/7** | 0/2 |
| neo4j | 6/6 | 0/7 | **2/2** (GDS) |
| arcade | 6/6 | 0/7 | 0/2 |

No backend covers everything: Fuseki searches but has no in-DB algorithms; Neo4j runs GDS but cannot
search yet. Judge search success by `triples_used > 0` / `evidence_nodes > 0` — never by whether an
answer came back. See `docs/DEBTS.md` §A for the order in which to port search onto an LPG backend.

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

## Roadmap (router-staged — [ADR-003](docs/ADR-003-router-staged.md))

- **0.1.0** (current): **R1 router** = registry + capability contract + `CapabilityError`; Fuseki backend +
  CLI + **Neo4j swap PoC** (6 core methods, cross-backend proof).
- **0.2.0**: **R2** = capability-based multi-backend routing (op → capable backend). Complete the Neo4j
  backend = **3rd layer** (remaining H items). Promote `OntologyStore` to the enforced full contract (DEBTS §G-Protocol).
- **0.3.0**: **R3** = router with dual-write / tenant routing — on top of ≥2 backends holding compatible data.

## Merge gate (live smoke — cleared 2026-08-16)

~~Deferred live smoke~~ **cleared.** `pytest -m live` passes 2/2 against a real Fuseki (docker, jena 5.1.0):
- **B4 write roundtrip**: insert → ASK True → delete → ASK False ✓
- **B5 merge roundtrip**: after two-sided move (subject + object), old URI residue **0** ✓

Verified inside a throwaway dataset (`graphstore_smoke`), then dropped — the live `xgen` dataset was untouched.
On top of the mock-transport + git-anchored golden tests, the real store e2e now passes too.

**Remaining pre-merge decision:** dependency-pin conversion (local path → git pin) + platform split
resolution — see [DEBTS.md §G](docs/DEBTS.md). Remote: `github.com/Createyouracccount/xgen-graphstore` (private).
