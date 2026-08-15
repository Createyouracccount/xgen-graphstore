"""graphstore CLI — 기존 메서드의 표면 노출만(새 기능 금지).

명령: health / ensure-dataset / count <graph> / snapshot-hash <graph>
접속 설정은 env(FUSEKI_URL 등) 또는 --base-url/--dataset 등으로.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from typing import Any, Dict

from xgen_graphstore.factory import create_store


def _store(args: argparse.Namespace):
    cfg: Dict[str, Any] = {"backend": "fuseki"}
    for k in ("base_url", "dataset", "username", "password"):
        v = getattr(args, k, None)
        if v:
            cfg[k] = v
    return create_store(cfg)


async def _health(args) -> int:
    ok = await _store(args).health_check()
    print(json.dumps({"health": ok}))
    return 0 if ok else 1


async def _ensure_dataset(args) -> int:
    ok = await _store(args).ensure_dataset()
    print(json.dumps({"ensure_dataset": ok}))
    return 0 if ok else 1


async def _count(args) -> int:
    n = await _store(args).get_triple_count(args.graph)
    print(json.dumps({"graph": args.graph, "triple_count": n}))
    return 0


async def _snapshot_hash(args) -> int:
    """그래프 트리플의 결정적 해시 — 기존 sparql_query 표면만 사용(새 기능 아님).

    정렬된 (s,p,o) 스트림을 sha256. 마이그레이션 전후 동등성 확인용.
    """
    store = _store(args)
    q = (
        f"SELECT ?s ?p ?o WHERE {{ GRAPH <{args.graph}> {{ ?s ?p ?o }} }} "
        f"ORDER BY ?s ?p ?o"
    )
    res = await store.sparql_query(q)
    h = hashlib.sha256()
    for b in res.get("results", {}).get("bindings", []):
        for k in ("s", "p", "o"):
            cell = b.get(k, {})
            h.update((cell.get("value", "") + "\x1f").encode("utf-8"))
        h.update(b"\x1e")
    print(json.dumps({"graph": args.graph, "snapshot_sha256": h.hexdigest()}))
    return 0


def _add_conn_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base-url")
    p.add_argument("--dataset")
    p.add_argument("--username")
    p.add_argument("--password")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="graphstore", description="xgen-graphstore CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name, fn, needs_graph in [
        ("health", _health, False),
        ("ensure-dataset", _ensure_dataset, False),
        ("count", _count, True),
        ("snapshot-hash", _snapshot_hash, True),
    ]:
        sp = sub.add_parser(name)
        _add_conn_args(sp)
        if needs_graph:
            sp.add_argument("graph", help="graph IRI")
        sp.set_defaults(_fn=fn)

    args = parser.parse_args(argv)
    return asyncio.run(args._fn(args))


if __name__ == "__main__":
    sys.exit(main())
