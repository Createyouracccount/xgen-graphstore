"""N-Triples 파싱 — 백엔드 공통.

`insert_data(graph_name, triple_lines)` 가 받는 입력 형식은 백엔드와 무관하므로
파싱 규칙도 한 곳에 둔다. 백엔드마다 다르게 파싱하면 **같은 입력이 백엔드별로 다른 그래프**가
되어 스왑 계약(교차 백엔드 동일 왕복)이 조용히 깨진다.

리소스 트리플과 리터럴 트리플을 분리해 돌려준다 — LPG 에서 전자는 엣지, 후자는 노드 property 다.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# `<s> <p> <o> .`
_TRIPLE_RE = re.compile(r"<([^>]+)>\s+<([^>]+)>\s+<([^>]+)>\s*\.")

# `<s> <p> "literal" .` — 언어태그(@ko)·데이터타입(^^<...>) 허용, 이스케이프(\" \\) 처리.
_LITERAL_RE = re.compile(
    r'<([^>]+)>\s+<([^>]+)>\s+"((?:[^"\\]|\\.)*)"(?:@[\w-]+|\^\^<[^>]+>)?\s*\.'
)

# property 키로 쓸 수 있는 안전한 이름(URI localname). 쿼리 조립 시 주입 차단.
_SAFE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def localname(uri: str) -> str:
    """URI 끝 조각. `...#label` → `label`, `.../sourceChunk` → `sourceChunk`."""
    return uri.rsplit("#", 1)[-1] if "#" in uri else uri.rsplit("/", 1)[-1]


def unescape(v: str) -> str:
    return v.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n").replace("\\t", "\t")


def parse_triples(triple_lines: str) -> List[Tuple[str, str, str]]:
    """리소스-리소스 트리플 → [(s, p, o), ...]. LPG 에서 엣지가 된다."""
    return _TRIPLE_RE.findall(triple_lines)


def parse_literals(triple_lines: str) -> List[Dict[str, str]]:
    """리터럴 트리플 → [{s, key, val}, ...]. LPG 에서 노드 property 가 된다.

    키로 부적합한 술어(하이픈 등)는 건너뛴다 — 엉뚱한 키로 조용히 저장되는 것보다 낫다.
    ⚠️ 같은 (s, key) 에 값이 여럿일 수 있다(RDF 다중값). 호출부는 **덮어쓰지 말고 누적**할 것.
    실측 사고: coOccursWith 의 rdfs:label 이 "함께언급"·"co-occurs with" 둘 다였는데
    단일값으로 저장해 검색 결과 149건이 조용히 소실됐다.
    """
    out: List[Dict[str, str]] = []
    for (s, p, v) in _LITERAL_RE.findall(triple_lines):
        key = localname(p)
        if not _SAFE_KEY_RE.match(key):
            continue
        out.append({"s": s, "key": key, "val": unescape(v)})
    return out


def group_literals_by_key(literals: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    """키별 배치로 묶는다 — 동적 property 키는 쿼리 파라미터로 못 주므로 키마다 나눠 실행."""
    by_key: Dict[str, List[Dict[str, str]]] = {}
    for r in literals:
        by_key.setdefault(r["key"], []).append({"s": r["s"], "v": r["val"]})
    return by_key
