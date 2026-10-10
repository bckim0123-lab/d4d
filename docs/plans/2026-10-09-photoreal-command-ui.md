# SPIDER Photoreal Command UI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** SPIDER 지휘소를 로컬 정사영상 중심의 실사형 항공우주 관제 UI로 개선하면서 기존 기능과 접근성을 유지한다.

**Architecture:** 기존 React 단일 앱과 FastAPI 계약은 유지한다. 지도 배경·HUD·계측 컴포넌트를 프론트 계층으로 추가하고, 모든 실사 자산은 `web/public`에서 오프라인 제공한다. 작성과 평가는 분리하며 Grok 구현 뒤 빌드와 브라우저 검증으로 승인한다.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind/CSS, SVG, lucide-react, FastAPI

---

### Task 1: 실사 지도 자산과 레이어 계약

**Files:**
- Create: `web/public/terrain/README.md`
- Create: `web/public/terrain/spider-ortho.webp`
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`

**Steps:**
1. 라이선스와 생성 목적을 `terrain/README.md`에 기록한다.
2. 회갈색 산업 지형, 도로, 건물, 식생이 포함된 정사영상 스타일 WebP를 추가한다.
3. `MapView`에 배경·등고 음영·위험·경로·통신·객체 순서의 레이어 구조를 만든다.
4. 배경 로딩 실패 시 기존 격자 배경이 유지되는지 확인한다.
5. `npm run build`를 실행하고 성공을 확인한다.

### Task 2: 지도 중심 관제 레이아웃

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`

**Steps:**
1. 데스크톱에서 지도 폭이 가시 영역의 60% 이상이 되도록 레이아웃을 재구성한다.
2. 우측 임무 레일에 속성·AI 패치·에이전트 추적·승인을 탭 또는 적층 패널로 배치한다.
3. KPI를 하단 시스템 스트립으로 이동해 지도 시야를 방해하지 않게 한다.
4. 기존 버튼과 API 호출이 모두 남아 있는지 확인한다.
5. 1440×900과 1920×1080에서 주요 동작이 첫 화면에 보이는지 브라우저로 확인한다.

### Task 3: 항공우주 계측과 재질

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`

**Steps:**
1. 링크·배터리·성공률·전달률·임무 시간을 숫자와 미니 게이지로 표현한다.
2. 무광 패널, 적층 유리, 얇은 베젤, 노이즈, 비네팅을 CSS로 추가한다.
3. 계측값이 없는 상태는 `--`와 `NO DATA`로 표시하고 가짜 수치를 만들지 않는다.
4. 텍스트 대비와 키보드 포커스를 확인한다.
5. `npm run build`를 실행한다.

### Task 4: 온라인/EMP 오프라인 모드 연출

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`

**Steps:**
1. EMP 드릴 재생 상태를 앱 루트의 `data-mode` 또는 클래스에 연결한다.
2. 온라인 청록 조명에서 오프라인 호박 HUD로 전환한다.
3. `LINK LOST`, 둥지 개방, 카드 판독, 교신, 귀환 이벤트를 지도와 상태 바에 동기화한다.
4. `prefers-reduced-motion`에서 전환 애니메이션이 제거되는지 확인한다.
5. 0.5×/1×/2× 속도와 샘플 선택 회귀를 확인한다.

### Task 5: 에이전트 추적과 승인 UX

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`

**Steps:**
1. SSE TraceStep을 지도 오버레이와 우측 추적 레일에 동시에 반영한다.
2. 제안 태그·BLE→광학 역할 전환을 투영 링과 명시적 텍스트로 표시한다.
3. 초안·승인·stale 상태를 색뿐 아니라 문구와 아이콘으로 표현한다.
4. 승인 전 패키지 링크를 비활성화하고 승인 후에만 활성화한다.
5. 실제 계획 생성과 승인 API를 브라우저에서 실행한다.

### Task 6: 반응형·접근성·회귀 검증

**Files:**
- Modify: `web/src/styles.css`
- Test: `tests/test_web.py`

**Steps:**
1. 860px 이하에서 지도 우선 단일 열과 하단 탐색을 유지한다.
2. 모든 아이콘 버튼의 접근성 이름과 폼 레이블을 확인한다.
3. `npm run build`를 실행해 TypeScript 오류가 없음을 확인한다.
4. `npm audit --audit-level=high`을 실행해 high 취약점 0을 확인한다.
5. `python -m unittest discover -s tests -q`를 실행해 전체 테스트 통과를 확인한다.
6. 브라우저에서 전장 로드, 자연어 diff, 계획 SSE, 승인, 드릴, BLE 미지원 상태를 확인한다.

### Task 7: 평가와 개선 기록

**Files:**
- Create: `artifacts/ui-review.md`
- Modify: `artifacts/README.md`

**Steps:**
1. 구현자가 변경 파일과 의도, 알려진 제한을 기록한다.
2. 평가자가 정보 계층, 실사감, 가독성, 기능 회귀를 별도로 점검한다.
3. 최대 2회 수정 루프를 수행하고 수렴하지 않으면 최선 버전과 차단 사유를 기록한다.
4. 최종 스크린샷, 빌드·테스트 결과, 재검토 조건을 `ui-review.md`에 남긴다.

