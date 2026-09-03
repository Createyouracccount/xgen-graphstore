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
# ⚠️ 태그는 **캡처**한다. 예전엔 비캡처로 흘려 LPG 에 언어 정보가 남지 않았고, 원본 browse
# 질의의 FILTER(LANG(?x) = "ko" || LANG(?x) = "") 를 재현할 수 없었다(DEBTS §D-2).
# RDF 상 언어태그와 데이터타입은 배타라 둘 중 하나만 채워진다(미기재 시 둘 다 "").
_LITERAL_RE = re.compile(
    r'<([^>]+)>\s+<([^>]+)>\s+"((?:[^"\\]|\\.)*)"(?:@([\w-]+)|\^\^<([^>]+)>)?\s*\.'
)

# property 키로 쓸 수 있는 안전한 이름(URI localname). 쿼리 조립 시 주입 차단.
_SAFE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ── RDF 어휘 + 검색 공통 상수 (백엔드 무관) ──
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
RDFS_SUBCLASS = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
RDFS_DOMAIN = "http://www.w3.org/2000/01/rdf-schema#domain"
OWL_CLASS = "http://www.w3.org/2002/07/owl#Class"
OWL_EQUIVALENT_CLASS = "http://www.w3.org/2002/07/owl#equivalentClass"
OWL_OBJECT_PROPERTY = "http://www.w3.org/2002/07/owl#ObjectProperty"
OWL_DATATYPE_PROPERTY = "http://www.w3.org/2002/07/owl#DatatypeProperty"
# 원본 SPARQL 의 `FILTER(?t IN (owl:ObjectProperty, owl:DatatypeProperty))` 등가.
PROPERTY_TYPES = [OWL_OBJECT_PROPERTY, OWL_DATATYPE_PROPERTY]
NS_DOMAIN = "https://w3id.org/xgen-domain#"

# 원본 SPARQL 의 _PRED_FILTER 등가 — 시드 결과에서 제외할 술어(구조·프로비넌스).
# 백엔드마다 다르게 두면 같은 질의가 백엔드별로 다른 결과를 내므로 여기 한 곳에서 정의한다.
EXCLUDED_PREDS = [
    RDF_TYPE, RDFS_LABEL,
    f"{NS_DOMAIN}sourceChunk", f"{NS_DOMAIN}sourceDocument", f"{NS_DOMAIN}scsContextSummary",
]


def parse_pin(pin: str) -> List[str]:
    """호출부가 조립한 술어라벨 목록(`"a", "b"`) → 리스트. 원본 FILTER(STR(?pl) IN (...)) 등가."""
    return [unescape(v) for v in re.findall(r'"((?:[^"\\]|\\.)*)"', pin or "")]


def localname(uri: str) -> str:
    """URI 끝 조각. `...#label` → `label`, `.../sourceChunk` → `sourceChunk`."""
    return uri.rsplit("#", 1)[-1] if "#" in uri else uri.rsplit("/", 1)[-1]


def unescape(v: str) -> str:
    return v.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n").replace("\\t", "\t")


def parse_triples(triple_lines: str) -> List[Tuple[str, str, str]]:
    """리소스-리소스 트리플 → [(s, p, o), ...]. LPG 에서 엣지가 된다."""
    return _TRIPLE_RE.findall(triple_lines)


def parse_literals(triple_lines: str) -> List[Dict[str, str]]:
    """리터럴 트리플 → [{s, key, val, p, lang, dtype}, ...]. LPG 에서 노드 property 가 된다.

    키로 부적합한 술어(하이픈 등)는 건너뛴다 — 엉뚱한 키로 조용히 저장되는 것보다 낫다.
    ⚠️ 같은 (s, key) 에 값이 여럿일 수 있다(RDF 다중값). 호출부는 **덮어쓰지 말고 누적**할 것.
    실측 사고: coOccursWith 의 rdfs:label 이 "함께언급"·"co-occurs with" 둘 다였는데
    단일값으로 저장해 검색 결과 149건이 조용히 소실됐다.

    `key` 는 localname 이라 **손실 있는 축약**이다(다른 네임스페이스의 같은 이름이 한 칸에
    뭉친다). 원본은 `p` 에 그대로 둔다 — `node_properties` 가 `?p` 를 URI 로 돌려줘야 하는데
    localname 에서 `NS_DOMAIN + key` 로 되살리면 네임스페이스를 **추측**하는 것이라
    '회색지대 기본값 금지'에 어긋난다(DEBTS §D-2).

    - `p`     : 술어 URI 원본 (항상 존재)
    - `lang`  : 언어태그 without '@'. 없으면 ""
    - `dtype` : 데이터타입 URI. 없으면 "" (RDF 상 lang 과 배타)
    """
    out: List[Dict[str, str]] = []
    for (s, p, v, lang, dtype) in _LITERAL_RE.findall(triple_lines):
        key = localname(p)
        if not _SAFE_KEY_RE.match(key):
            continue
        out.append({
            "s": s, "key": key, "val": unescape(v),
            "p": p, "lang": lang, "dtype": dtype,
        })
    return out


def group_literals_by_key(literals: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    """키별 배치로 묶는다 — 동적 property 키는 쿼리 파라미터로 못 주므로 키마다 나눠 실행.

    `p`/`lang`/`dtype` 를 함께 통과시킨다. 파서가 보존한 것을 여기서 다시 버리면
    수리가 무의미해진다(기존 소비자는 `s`/`v` 만 읽으므로 동작 불변).
    """
    by_key: Dict[str, List[Dict[str, str]]] = {}
    for r in literals:
        by_key.setdefault(r["key"], []).append({
            "s": r["s"], "v": r["val"],
            "p": r["p"], "lang": r["lang"], "dtype": r["dtype"],
        })
    return by_key
