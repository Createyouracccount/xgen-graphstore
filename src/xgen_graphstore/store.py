"""OntologyStore — 백엔드 중립 온톨로지 저장소 인터페이스 (2층 이관).

목적: xgen-documents 가 그래프 저장소(현재 Apache Jena Fuseki)와 대화하는 지점을,
인라인 SPARQL 문자열이 아니라 **의미 단위 메서드**로 걷어내기 위한 seam.
이렇게 해두면 장차 LPG(Neo4j/AGE) 백엔드를 인터페이스 뒤에 꽂을 수 있다.

⚠️ 이번 단계(2층)는 **동작 보존 리팩터링**이다:
- 각 의미 메서드는 기존 인라인이 만들던 것과 **바이트 동일한 SPARQL** 을 방출한다.
- 쿼리 개선/정리/최적화 금지. 에러 처리·재시도·트랜잭션 경계 변경 금지.
- LPG 구현·라우터·설정 옵션 추가는 이번 범위가 아니다(3층).

설계 메모(백엔드 누수 = 3층 부채, 지금 고치지 않고 기록만):
- `graph_name` 이 모든 메서드에 관통한다 → RDF named graph 개념 누수. LPG 엔 없다.
- 반환 형태가 현재 Fuseki JSON 바인딩 그대로다 → 파싱 코드가 호출부에 남아있다.
  이번엔 그 파싱까지 함께 옮겨 "파싱 등가"를 게이트로 검증한다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class OntologyStore(Protocol):
    """그래프 저장소 의미 연산 계약.

    구현체(현재 FusekiBackend)는 이 메서드들을 제공한다. 배치 이관이 진행되며
    필요한 메서드만 점진적으로 추가한다(투기적 선언 금지 — CLAUDE.md 단순성 원칙).
    """

    # ── B1: graph_rag 순수 READ ──
    async def node_properties(self, graph_name: str, node_uri: str) -> List[Dict[str, Any]]:
        ...

    async def property_values(
        self, graph_name: str, property_uri: str, limit: int
    ) -> List[Dict[str, Any]]:
        ...

    async def neighbors(
        self, graph_name: str, node_uri: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        ...

    async def triple_exists(
        self, graph_name: str, s: str, p: str, o: str
    ) -> bool:
        ...

    async def count_node_triples(self, graph_name: str, node_uri: str) -> int:
        ...
