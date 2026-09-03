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

async def _seed_classes_trace(store, G, other_G, mode):
    """클래스 시드 왕복: 폐포 2종이 필요한 데이터 + 다른 그래프 오염원.

    한 픽스처로 아래를 전부 건드린다(변이를 넣었을 때 실제로 걸리도록):
    - 정방향 동치 `A≡B` → i2 가 동치로만 도달
    - **역방향 동치** `D≡A` → i4 는 `^owl:equivalentClass` 로만 도달. 없으면 무방향 `-` 를
      방향 `->` 로 바꿔도 통과한다.
    - 이행 `C⊑B` → i3 가 이행으로만 도달
    - **다이아몬드** `E⊑C` + `E⊑B` → i5 에 도달하는 경로가 2개. 없으면
      `count(DISTINCT i)`→`count(i)`, `collect(DISTINCT …)`→`collect(…)` 변이가 안 걸린다
      (SPARQL `*` 는 도달가능성, Cypher `*0..` 는 경로 열거라 중복이 생긴다).
    - **두 번째 매칭 클래스** `A2`(같은 라벨, 인스턴스 1개) → 결과 행이 2개라
      `LIMIT 3`→`LIMIT 1` 변이가 걸린다. n 이 달라 정렬은 결정적이다.
    - other_G 에 같은 클래스의 인스턴스를 넣어 graph 격리도 함께 본다.

    ⚠️ 라벨은 **고유 토큰**(zq9m)이다. `_ft_nodes` 는 graph 스코프가 없어 전문색인이
    전역이므로, 흔한 낱말을 쓰면 다른 그래프·이전 실행의 동명 노드가 상위 30을 채워
    시드가 밀려난다(실측: '국가'로 두 번째 클래스가 사라짐). 토큰을 바꾸지 말 것.

    ⚠️ 미덮음: 리터럴(label)은 아직 graph 스코프가 없다 — label 이 **다른 graph 에만**
    있으면 LPG 가 Fuseki 보다 더 준다. 그 축과 한국어 토큰화 차이는 이 테스트 밖이다.
    """
    RDFS, RDF_, OWL = ("http://www.w3.org/2000/01/rdf-schema#",
                       "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                       "http://www.w3.org/2002/07/owl#")
    cls = lambda u, l: (f'<{NS}{u}> <{RDF_}type> <{OWL}Class> . '
                        f'<{NS}{u}> <{RDFS}label> "{l}" .')
    ins = lambda u, c, l: (f'<{IN}{u}> <{RDF_}type> <{NS}{c}> . '
                           f'<{IN}{u}> <{RDFS}label> "{l}" .')
    t = " ".join([
        cls("zq9mA", "zq9m base"), cls("zq9mA2", "zq9m base"),
        # B·C·D·E 는 시드가 아니다 — 폐포로만 도달해야 하므로 검색 토큰을 안 넣는다.
        cls("zq9mB", "beta"), cls("zq9mC", "gamma"),
        cls("zq9mD", "delta"), cls("zq9mE", "epsilon"),
        f'<{NS}zq9mA> <{OWL}equivalentClass> <{NS}zq9mB> .',      # 정방향 동치
        f'<{NS}zq9mD> <{OWL}equivalentClass> <{NS}zq9mA> .',      # 역방향 동치
        f'<{NS}zq9mC> <{RDFS}subClassOf> <{NS}zq9mB> .',          # 이행
        f'<{NS}zq9mE> <{RDFS}subClassOf> <{NS}zq9mC> .',          # 다이아몬드 경로 1
        f'<{NS}zq9mE> <{RDFS}subClassOf> <{NS}zq9mB> .',          # 다이아몬드 경로 2
        ins("zq9mi1", "zq9mA", "i1"), ins("zq9mi2", "zq9mB", "i2"),
        ins("zq9mi3", "zq9mC", "i3"), ins("zq9mi4", "zq9mD", "i4"),
        ins("zq9mi5", "zq9mE", "i5"), ins("zq9mi6", "zq9mA2", "i6")])
    t2 = " ".join([cls("zq9mA", "zq9m base"), ins("zq9mLeak", "zq9mA", "leak")])
    for g, lines in ((G, t), (other_G, t2)):
        await store.delete_data(g, lines)
        await store.insert_data(g, lines)
    try:
        await store.ensure_fulltext_index()   # Fuseki 는 jena-text 설정이라 미보유
    except Exception:
        pass
    res = await store.seed_classes_by_fulltext(G, "zq9m", mode=mode)
    rows = res.get("results", {}).get("bindings", [])

    def _v(row, k):
        d = row.get(k)
        return d.get("value") if isinstance(d, dict) else d
    return [(_v(r, "cl"), str(_v(r, "n")), tuple(sorted((_v(r, "insts") or "").split(" | "))))
            for r in rows]


@pytest.mark.parametrize("mode,expected", [
    # A: 직접 i1 / 동치 i2·i4(역방향) / 이행 i3·i5(다이아몬드) → 5
    # A2: 자기 인스턴스 i6 1개 (LIMIT 변이 검출용 두 번째 행)
    ("closure", [("zq9m base", "5", ("i1", "i2", "i3", "i4", "i5")),
                 ("zq9m base", "1", ("i6",))]),
    # direct 는 이행 폐포가 없다 — 동치까지만(i1·i2·i4)
    ("direct",  [("zq9m base", "3", ("i1", "i2", "i4")),
                 ("zq9m base", "1", ("i6",))]),
])
def test_swap_seed_classes_identical(mode, expected):
    """클래스 전수 시드 등가 — 폐포 2종 + graph 격리. 3백엔드 동일해야 한다.

    other_G 의 `leak` 이 섞이면 격리 파손이다 — expected 에 없으므로 자동으로 걸린다.
    """
    G, OG = f"{IN}swap-seedcls", f"{IN}swap-seedcls-other"
    fus = _run(_seed_classes_trace(_fuseki(), G, OG, mode))
    assert fus == expected, f"fuseki trace off: {fus}"
    for name, mk in (("neo4j", _neo4j), ("arcade", _arcade)):
        got = _run(_seed_classes_trace(mk(), G, OG, mode))
        assert got == fus, f"스왑 불일치 {name}={got} vs fuseki={fus}"
