"""no-op CallTimer — 공유 커널의 선택적 호출 계측 훅.

xgen-documents 는 SPARQL/PG/Qdrant/LLM 호출을 request_id 로 묶어 추적하는
call_logger.CallTimer 를 쓴다. 그건 documents 의 관측 시스템(그래프 store 범위 밖)이라
패키지로 끌고 오지 않는다. 대신 동일 인터페이스의 no-op 를 기본 제공하고,
호스트가 원하면 set_call_timer() 로 자기 구현을 주입한다.

계약(원본 CallTimer 와 동일 표면):
    with CallTimer(kind, caller=..., payload=...) as t:
        t.set_result({...})
        t.status = "error"   # 선택
"""

from __future__ import annotations

from typing import Any, Callable, Optional


class _NoopCallTimer:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.status: Optional[str] = None

    def __enter__(self) -> "_NoopCallTimer":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def set_result(self, *args: Any, **kwargs: Any) -> None:
        pass


# 호스트가 주입 가능한 팩토리. 기본은 no-op.
_factory: Callable[..., Any] = _NoopCallTimer


def set_call_timer(factory: Callable[..., Any]) -> None:
    """호스트가 자기 CallTimer 구현을 주입(예: documents 의 request-tracing)."""
    global _factory
    _factory = factory


def CallTimer(*args: Any, **kwargs: Any) -> Any:  # noqa: N802 (원본 이름 유지)
    return _factory(*args, **kwargs)
