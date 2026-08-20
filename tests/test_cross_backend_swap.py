"""교차 백엔드 스왑 증명 — 같은 계약, 서로 다른 저장엔진(SPARQL vs Cypher).

`create_store({"backend": X})` 로 **백엔드만 바꿔가며** 동일한 왕복을 돌리고,
관측 결과(trace)가 두 백엔드에서 **바이트 동일**한지 단언한다.
이게 "graphstore 만 갈아끼우면 RDF↔LPG 스왑이 된다"의 실증(말 아닌 통과 테스트).

필요: 실제 Fuseki + 실제 Neo4j. `pytest -m live`.
env: FUSEKI_URL/FUSEKI_DATASET/FUSEKI_ADMIN_USER/FUSEKI_ADMIN_PASSWORD,
     NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD.
"""

import asyncio
import os

import pytest

pytestmark = pytest.mark.live

NS = "https://w3id.org/xgen-domain#"
IN = "https://w3id.org/xgen-instance#"


def _fuseki():
    from xgen_graphstore import create_store
    return create_store({
        "backend": "fuseki",
        "base_url": os.getenv("FUSEKI_URL", "http://localhost:3030"),
        "dataset": os.getenv("FUSEKI_DATASET", "xgen"),
        "username": os.getenv("FUSEKI_ADMIN_USER", "admin"),
        "password": os.getenv("FUSEKI_ADMIN_PASSWORD", "admin"),
    })


def _neo4j():
    from xgen_graphstore import create_store
    return create_store({
        "backend": "neo4j",
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        "username": os.getenv("NEO4J_USER", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "testpassword"),
    })


def _arcade():
    from xgen_graphstore import create_store
    return create_store({
        "backend": "arcade",
        "base_url": os.getenv("ARCADE_URL", "http://localhost:2480"),
        "database": os.getenv("ARCADE_DB", "xgen"),
        "username": os.getenv("ARCADE_USER", "root"),
        "password": os.getenv("ARCADE_PASSWORD", "arcadepw"),
    })


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _b4_trace(store, G):
    """B4 write 왕복: insert→exists/count→delete→exists/count."""
    S, P, O = f"{IN}삼성", f"{NS}인수", f"{IN}하만"
    line = f"<{S}> <{P}> <{O}> ."
    await store.delete_data(G, line)  # 초기화
    t = []
    await store.insert_data(G, line)
    t.append(await store.triple_exists(G, S, P, O))      # True
    t.append(await store.count_node_triples(G, S))       # 1
    await store.delete_data(G, line)
    t.append(await store.triple_exists(G, S, P, O))      # False
    t.append(await store.count_node_triples(G, S))       # 0
    return t


async def _b5_trace(store, G):
    """B5 병합 왕복: 2면 이동 후 구 URI 잔존 0, canonical 로 이관."""
    dup, can, other = f"{IN}한국마사회를", f"{IN}한국마사회", f"{IN}경마장"
    await store.delete_data(G, f"<{dup}> <{NS}위치> <{other}> .")   # 초기화
    await store.delete_data(G, f"<{other}> <{NS}운영> <{dup}> .")
    await store.delete_data(G, f"<{can}> <{NS}위치> <{other}> .")
    await store.delete_data(G, f"<{other}> <{NS}운영> <{can}> .")
    await store.insert_data(G, f"<{dup}> <{NS}위치> <{other}> . <{other}> <{NS}운영> <{dup}> .")
    t = []
    t.append(await store.count_node_triples(G, dup))     # 2 (주어 1 + 목적어 1)
    await store.merge_move_subject(G, dup, can)
    await store.merge_move_object(G, dup, can)
    t.append(await store.count_node_triples(G, dup))     # 0 (구 URI 소멸)
    t.append(await store.count_node_triples(G, can))     # 2 (canonical 로 이관)
    return t


def test_swap_b4_write_roundtrip_identical():
    fus = _run(_b4_trace(_fuseki(), f"{IN}swap-b4"))
    neo = _run(_b4_trace(_neo4j(), f"{IN}swap-b4"))
    assert fus == [True, 1, False, 0], f"fuseki trace off: {fus}"
    assert neo == fus, f"스왑 불일치 neo4j={neo} vs fuseki={fus}"


def test_swap_b5_merge_roundtrip_identical():
    fus = _run(_b5_trace(_fuseki(), f"{IN}swap-b5"))
    neo = _run(_b5_trace(_neo4j(), f"{IN}swap-b5"))
    assert fus == [2, 0, 2], f"fuseki trace off: {fus}"
    assert neo == fus, f"스왑 불일치 neo4j={neo} vs fuseki={fus}"


def test_swap_b4_fuseki_vs_arcade_identical():
    """ArcadeDB(채택 후보) 교차 스왑 — B4 write 왕복이 Fuseki와 동일."""
    fus = _run(_b4_trace(_fuseki(), f"{IN}swap-arc-b4"))
    arc = _run(_b4_trace(_arcade(), f"{IN}swap-arc-b4"))
    assert fus == [True, 1, False, 0], f"fuseki trace off: {fus}"
    assert arc == fus, f"스왑 불일치 arcade={arc} vs fuseki={fus}"


def test_swap_b5_fuseki_vs_arcade_identical():
    """ArcadeDB 교차 스왑 — B5 병합 왕복(2면 이동 후 구 URI 잔존 0)이 Fuseki와 동일."""
    fus = _run(_b5_trace(_fuseki(), f"{IN}swap-arc-b5"))
    arc = _run(_b5_trace(_arcade(), f"{IN}swap-arc-b5"))
    assert fus == [2, 0, 2], f"fuseki trace off: {fus}"
    assert arc == fus, f"스왑 불일치 arcade={arc} vs fuseki={fus}"
