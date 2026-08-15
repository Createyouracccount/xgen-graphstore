# ADR-001 — xgen-graphstore는 공유 커널(라이브러리), HTTP 서비스 아님

- 상태: 채택 (2026-08-15)

## 결정
xgen-graphstore는 Python 서비스가 **라이브러리로 의존**하는 공유 커널이다(xgen-sdk와 같은 계층).
새 HTTP 서비스로 만들지 않는다.

## 이유
CLAUDE.md §5 XGEN 지도: 게이트웨이의 모듈→서비스 매핑이 `config/services.{yaml,local.yaml,
docker.yaml}` **3벌 수동 복제**다. 새 HTTP 엔드포인트를 여는 순간:
- 게이트웨이 services.yaml 3벌에 라우트 추가(누락 시 404, 빌드타임 신호 없음),
- `X-User-Id` 헤더 계약,
- 프론트 `packages/api-client` 수작성 타입,
까지 blast radius가 번진다. 그래프 저장소는 documents 하위의 내부 관심사이므로
라이브러리 의존이 결합면을 최소화한다.

## 결과
- 배포: 사설 PyPI 핀(초기엔 로컬 경로/git 의존).
- 관측(call-logging)은 주입식(`set_call_timer`) — 호스트 관측 시스템을 커널이 끌고 오지 않는다.
