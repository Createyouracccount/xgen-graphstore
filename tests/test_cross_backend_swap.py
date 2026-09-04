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


def test_arcade_fulltext_term_is_not_injectable():
    """검색어가 질의를 바꾸지 못한다 — ArcadeDB 전문검색은 SQL 문자열 보간이라 회귀하기 쉽다.

    양성 대조로 배선이 살아 있음을 먼저 보이고(매칭되는 낱말 → 결과 있음),
    매칭이 0인 낱말에 주입 payload 를 붙였을 때 여전히 0인지 본다.
    수정 전에는 30행이 나왔다(질의가 변조되어 전체가 반환됨).
    """
    store = _arcade()
    G = f"{IN}swap-seedcls"
    # 픽스처를 깔아 양성 대조 대상을 만든다.
    _run(_seed_classes_trace(store, G, f"{IN}swap-seedcls-other", "closure"))
    hit = _run(store._ft_nodes("zq9m", 30))
    assert hit, "양성 대조 실패 — 전문검색 배선이 죽었다면 아래 0 은 의미가 없다"
    for payload in ("nomatchxyz' OR '1'='1", 'nomatchxyz" OR "1"="1'):
        got = _run(store._ft_nodes(payload, 30))
        assert got == [], f"검색어로 질의가 변조됐다: {payload!r} → {len(got)}건"


_P1 = f"{NS}wk7pAcquire"
_P2 = f"{NS}wk7pLocate"
_COOC = f"{NS}coOccursWith"


async def _graph_search_trace(store, G, other_G, method, args):
    """GRAPH_SEARCH 7종 공통 픽스처 — 관계 3개 + 동시출현 1개 + 청크 소속.

    `wk7A -acquires-> wk7B -located in-> wk7C -acquires-> wk7D` 사슬에
    `wk7A -coOccursWith-> wk7C` 를 얹는다. 동시출현은 슬롯이 갈린다:
    connectivity(정밀)는 **제외**, broad(recall 폴백)는 **포함**, chunk_cooccurrence 는
    그것만 본다. 셋이 같은 데이터에서 다른 답을 내야 한다 — 하나라도 필터가 빠지면 걸린다.

    other_G 에 같은 라벨 토큰의 관계를 하나 더 둔다 — graph 필터가 빠지면 시드 양끝이
    모두 매칭돼 결과에 섞인다(격리 변이 검출용).

    라벨 토큰은 고유(wk7)여야 한다. `_ft_nodes` 는 graph 스코프가 없어 전문색인이 전역이다.
    """
    RDFS, NS_ = "http://www.w3.org/2000/01/rdf-schema#", NS
    def node(u, label, chunks=()):
        out = [f'<{IN}{u}> <{RDFS}label> "{label}" .']
        out += [f'<{IN}{u}> <{NS_}sourceChunk> "{c}" .' for c in chunks]
        return " ".join(out)
    t = " ".join([
        node("wk7A", "wk7 alpha", ["ck7-1"]), node("wk7B", "wk7 beta", ["ck7-1"]),
        node("wk7C", "wk7 gamma", ["ck7-2"]), node("wk7D", "wk7 delta"),
        f'<{_P1}> <{RDFS}label> "acquires" .', f'<{_P2}> <{RDFS}label> "located in" .',
        f'<{_COOC}> <{RDFS}label> "co-occurs with" .',
        f'<{IN}wk7A> <{_P1}> <{IN}wk7B> .', f'<{IN}wk7B> <{_P2}> <{IN}wk7C> .',
        f'<{IN}wk7C> <{_P1}> <{IN}wk7D> .', f'<{IN}wk7A> <{_COOC}> <{IN}wk7C> .'])
    t2 = " ".join([
        node("wk7A", "wk7 alpha"), node("wk7E", "wk7 epsilon", ["ck7-1"]),
        f'<{IN}wk7A> <{_P1}> <{IN}wk7E> .'])
    for g, lines in ((G, t), (other_G, t2)):
        await store.delete_data(g, lines)
        await store.insert_data(g, lines)
    try:
        await store.ensure_fulltext_index()
    except Exception:
        pass
    res = await getattr(store, method)(G, *args)
    rows = res.get("results", {}).get("bindings", []) if isinstance(res, dict) else res
    return sorted(tuple(sorted((k, (v.get("value") if isinstance(v, dict) else v))
                               for k, v in (r or {}).items())) for r in rows)


@pytest.mark.parametrize("method,args,rows", [
    ("predicate_labels",                   (),                          3),
    ("seed_chunk_relations",               ('"ck7-1" "ck7-2"', 40),     3),
    ("seed_chunk_cooccurrence",            ('"ck7-1" "ck7-2"', 40),     1),
    # 정밀 시드 — 동시출현 제외라 3 (포함하면 4). 이 1 차이가 필터 누락을 잡는다.
    ("seed_connectivity_relations",        ("wk7", 40),                 3),
    # recall 폴백 — 동시출현 포함이라 4
    ("seed_relations_broad",               ("wk7", 40),                 4),
    ("seed_relations_by_fulltext_forward", ("wk7", '"acquires"'),       2),
    ("seed_relations_by_fulltext_reverse", ("wk7", '"acquires"'),       2),
])
def test_swap_graph_search_identical(method, args, rows):
    """GRAPH_SEARCH 등가 — 3백엔드가 같은 데이터에 같은 답을 내야 한다.

    `rows` 는 양성 대조다. 0 이 아니어야 배선이 살아 있다는 뜻이고, 그래야
    "3백엔드 동일"이 의미를 갖는다(양쪽 다 죽어도 동일은 나온다).
    """
    G, OG = f"{IN}swap-gsearch", f"{IN}swap-gsearch-other"
    fus = _run(_graph_search_trace(_fuseki(), G, OG, method, args))
    assert len(fus) == rows, f"fuseki {method} 행수 {len(fus)} != {rows}: {fus}"
    for name, mk in (("neo4j", _neo4j), ("arcade", _arcade)):
        got = _run(_graph_search_trace(mk(), G, OG, method, args))
        assert got == fus, f"스왑 불일치 {method}/{name}={got} vs fuseki={fus}"


@pytest.mark.xfail(strict=True, reason="DEBTS H-1: RDF 는 리터럴도 트리플, LPG 는 노드 속성 "
                                       "— count_node_triples 가 갈린다. 모델 변경 필요")
def test_swap_count_node_triples_with_literals():
    """리터럴이 붙은 노드의 트리플 수 — 지금은 갈린다.

    게이트의 B4 픽스처는 관계만 있어 이 축을 못 본다. 실데이터의 노드는 항상
    `rdfs:label` 을 갖는다. 실측:
        관계만        fuseki 1 / neo4j 1 / arcade 1   ✓
        관계+라벨1개  fuseki 2 / neo4j 1 / arcade 1   ✗
        관계+라벨2개  fuseki 3 / neo4j 1 / arcade 1   ✗
    LPG 가 노드 속성 값 개수를 함께 세면 맞출 수 있다(`uri` 는 식별자라 제외).
    고치면 이 테스트가 xfail→xpass 로 뒤집혀 strict 로 걸린다 — 그때 마크를 지울 것.
    """
    RDFS = "http://www.w3.org/2000/01/rdf-schema#"
    G = f"{IN}swap-litcount"
    t = f'<{IN}lc1> <{NS}lcP> <{IN}lc2> . <{IN}lc1> <{RDFS}label> "alpha" .'

    async def _trace(store):
        await store.delete_data(G, t)
        await store.insert_data(G, t)
        n = await store.count_node_triples(G, f"{IN}lc1")
        try:
            await store.close()
        except Exception:
            pass
        return n

    fus = _run(_trace(_fuseki()))
    assert fus == 2, f"fuseki trace off: {fus}"   # 관계 1 + 라벨 1
    for name, mk in (("neo4j", _neo4j), ("arcade", _arcade)):
        assert _run(_trace(mk())) == fus, f"리터럴 계층 불일치 {name}"
