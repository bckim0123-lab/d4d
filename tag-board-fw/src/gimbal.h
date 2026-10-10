#pragma once
// 카메라 팬·틸트 서보 (config.h GIMBAL_*). 50Hz 태스크가 목표 각도로 GIMBAL_SLEW_DEG_S 속도로 움직인다.
#include <stddef.h>

#include "gimbal_geom.h"

struct GimbalPose {
  float pan, tilt;  // 지금 서보에 보내는 각도(°)
  bool known;       // 각도를 앎 (꺼짐·보정 펄스 모드면 false → 위치 계산에 쓰지 않음)
  bool settled;     // 멈춘 뒤 GIMBAL_SETTLE_MS가 지남 → 이 각도로 계산한 위치를 제어에 써도 됨
};

bool gimbalBegin();                           // PWM 시작 + 기본 자세(팬 0°, 틸트 0°). 비활성이면 true
GimbalPose gimbalPose();                      // 비활성(고정 장착)이면 항상 0°/0°, known·settled
bool gimbalSet(float panDeg, float tiltDeg);  // 범위로 자른다. 비활성이면 false
bool gimbalRawUs(int panUs, int tiltUs);      // 장착 보정용 펄스 직접 지정 (각도 모름 상태가 됨)
bool gimbalOff();                             // 신호를 끊어 서보 힘을 뺌 (각도 모름 상태가 됨)
void gimbalJson(char *out, size_t n);         // {"type":"gimbal",...}
