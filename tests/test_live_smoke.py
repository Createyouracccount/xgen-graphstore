"""라이브 스모크 — 실제 Fuseki(docker) 필요. `pytest -m live` 로만 실행.

CI 기본은 목 테스트만. 이 파일은 -m live 마커로 분리된다.
이월 부채(B4 write 왕복 + B5 병합 왕복: 병합 후 구 URI 잔존 0)를 여기서 청산한다.
env: FUSEKI_URL(기본 http://localhost:3033) 등.
"""

import asyncio
import os

import pytest

pytestmark = pytest.mark.live

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
OWL = "http://www.w3.org/2002/07/owl#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
NS = "https://w3id.org/xgen-domain#"
IN = "https://w3id.org/xgen-instance#"


def _store():
    from xgen_graphstore import create_store
    return create_store({
        "backend": "fuseki",
        "base_url": os.getenv("FUSEKI_URL", "http://localhost:3033"),
        "dataset": os.getenv("FUSEKI_DATASET", "xgen"),
        "username": os.getenv("FUSEKI_ADMIN_USER", "admin"),
        "password": os.getenv("FUSEKI_ADMIN_PASSWORD", "smokepw"),
    })


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_live_write_roundtrip():
    """B4 이월: insert → ASK True → delete → ASK False."""
    b = _store()
    G = f"{IN}live-b4"
    S, P, O = f"{IN}삼성", f"{NS}인수", f"{IN}하만"
    assert _run(b.health_check()) is True
    _run(b.delete_data(G, f"<{S}> <{P}> <{O}> ."))  # 초기화
    _run(b.insert_data(G, f"<{S}> <{P}> <{O}> ."))
    assert _run(b.triple_exists(G, S, P, O)) is True
    _run(b.delete_data(G, f"<{S}> <{P}> <{O}> ."))
    assert _run(b.triple_exists(G, S, P, O)) is False


def test_live_merge_roundtrip_old_uri_gone():
    """B5 이월: 병합 후 구 URI 가 subject·object 양면에서 완전히 사라짐(잔존 0)."""
    b = _store()
    G = f"{IN}live-b5"
    dup, can, other = f"{IN}한국마사회를", f"{IN}한국마사회", f"{IN}경마장"
    # dup 이 subject(→other) 와 object(other→dup) 양쪽에 등장
    _run(b.insert_data(G, f"<{dup}> <{NS}위치> <{other}> . <{other}> <{NS}운영> <{dup}> ."))
    before = _run(b.count_node_triples(G, dup))
    assert before >= 2
    # 2면 이동
    _run(b.merge_move_subject(G, dup, can))
    _run(b.merge_move_object(G, dup, can))
    after = _run(b.count_node_triples(G, dup))
    assert after == 0, f"병합 후 구 URI 잔존 {after} (0이어야)"
