# SPIDER 실사형 항공우주 관제 UI 검토

## 상태

**사용 가능**

작성자와 평가자를 분리해 사양 검토와 코드 품질 검토를 완료했다.

## 산출물

- 구현: `web/src/App.tsx`, `web/src/styles.css`
- 정사영상: `web/public/terrain/spider-ortho.jpg`
- 자산 고지: `web/public/terrain/README.md`
- 설계: `docs/plans/2026-10-09-photoreal-command-ui-design.md`
- 구현 계획: `docs/plans/2026-10-09-photoreal-command-ui.md`
- 검토 화면: `artifacts/ui-review.png`

## 주요 결정

- 지도 중심 cockpit 구조와 우측 임무 레일, 하단 시스템 스트립
- 로컬 생성형 정사영상과 SVG 전술 레이어 합성
- 온라인 청록/EMP 오프라인 호박 모드
- 실제 데이터가 없으면 `--` 또는 `NO DATA`
- 승인 상태를 런 ID와 결속하고 package/card/drill을 서버에서도 차단
- 원격 원본과 편집 변형을 `scenarioDirty`로 분리
- 외부 폰트·지도 API 없이 오프라인 표시

## 검증 결과

- `npm run build`: 통과
- `npm audit --audit-level=high`: 취약점 0
- `python -m unittest discover -s tests -q`: 142개 통과
- IDE 린트: 오류 없음
- 사양 검토: PASS
- 코드 품질 검토: APPROVED
- 브라우저: 정사영상, SSE 계획, 제안 태그, KPI, 승인 잠금, 접근성 구조 확인

## 알려진 제한

- 정사영상은 실제 좌표나 전장을 나타내지 않는 생성형 데모 자산이다.
- 실물 XIAO NUS 연결은 장치가 있는 Chrome/Edge 환경에서 추가 확인해야 한다.
- 지도는 2D 로컬 좌표이며 실제 GIS/고도 데이터가 아니다.

## 재검토 조건

- 실제 정사영상 또는 GIS 공급자를 연결할 때
- 승인 상태/API 경로가 바뀔 때
- WebGL 3D 지형을 도입할 때
- FC·XIAO 실기 로그 형식이 바뀔 때

