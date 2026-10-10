#pragma once
// 카메라 팬·틸트 기하 계산 (아두이노 의존 없음 → test/gimbal_host_test.cpp에서 PC로 시험)
//
// 각도 규약 (기체 기준)
//   팬  0° = 기체 앞, + = 오른쪽으로 돌림 (위에서 보면 시계방향)
//   틸트 0° = 바로 아래 (기존 고정 장착 자세), + = 앞쪽으로 들어 올림, 90° = 정면 수평
// 기계 구조 가정: 아래 서보가 팬(수직축), 그 위 서보가 틸트.
//
// 기체 축: f = 앞, r = 오른쪽, d = 아래

#include <math.h>

struct Vec3 {
  float f, r, d;
};

// nadirFwd·nadirRight: 카메라가 바로 아래를 볼 때 기준의 광선 성분 (픽셀 오프셋 ÷ 초점거리,
// CAM_ROTATE_DEG 반영 후). 광축 성분은 1이다. 이 광선을 틸트 → 팬 순서로 돌려 기체 축으로 바꾼다.
inline Vec3 camRayToBody(float nadirFwd, float nadirRight, float panDeg, float tiltDeg) {
  const float k = (float)M_PI / 180.0f;
  const float ct = cosf(tiltDeg * k), st = sinf(tiltDeg * k);
  const float cp = cosf(panDeg * k), sp = sinf(panDeg * k);
  // 틸트: 오른쪽 축을 기준으로 광축(아래)을 앞쪽으로 들어 올린다
  const float f1 = nadirFwd * ct + st;
  const float d1 = -nadirFwd * st + ct;
  const float r1 = nadirRight;
  // 팬: 아래 축을 기준으로 오른쪽으로 돌린다
  return {f1 * cp - r1 * sp, f1 * sp + r1 * cp, d1};
}

// 착륙 지점의 기체 기준 수평 위치(cm).
//  - 틸트가 obliqueDeg보다 작거나 높이가 없으면: 태그 크기로 잰 광축 깊이 rangeCm × 광선 (기존 방식과 같음)
//  - 틸트가 크고 높이(MTF-01P)가 있으면: 광선과 지면의 교점 (비스듬히 보면 태그가 짧아 보여 크기 방식이 틀어짐)
// 지면 교점은 광선이 수평 위로 minDown(정규화한 아래 성분)보다 낮게 향할 때만 쓴다.
struct GroundFix {
  float fwd, right;
  bool ok;
  bool byHeight;
  float downFrac;  // 광선 방향의 아래 성분 (1 = 바로 아래, 0 = 수평)
};

inline GroundFix groundFix(const Vec3 &ray, float rangeCm, float heightCm, float tiltDeg, float obliqueDeg,
                           float minDown) {
  const float norm = sqrtf(ray.f * ray.f + ray.r * ray.r + ray.d * ray.d);
  GroundFix g = {0, 0, false, false, norm > 0 ? ray.d / norm : 0};
  if (heightCm > 0 && fabsf(tiltDeg) >= obliqueDeg) {
    if (g.downFrac < minDown) return g;  // 수평에 가까워 교점이 너무 멀거나 없음
    const float s = heightCm / ray.d;
    g.fwd = s * ray.f;
    g.right = s * ray.r;
    g.byHeight = true;
  } else {
    if (rangeCm <= 0) return g;
    g.fwd = rangeCm * ray.f;
    g.right = rangeCm * ray.r;
  }
  g.ok = true;
  return g;
}

// 서보 각도 → 펄스(µs). 범위 밖이면 자른다.
inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline float angleToUs(float deg, float zeroUs, float usPerDeg, float usMin, float usMax) {
  return clampf(zeroUs + deg * usPerDeg, usMin, usMax);
}
// 목표 쪽으로 최대 step 만큼 다가간다 (급격한 움직임 방지)
inline float approach(float cur, float target, float step) {
  if (target > cur + step) return cur + step;
  if (target < cur - step) return cur - step;
  return target;
}
