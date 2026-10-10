#include "gimbal.h"

#include <Arduino.h>
#include <stdio.h>

#include "config.h"

#if GIMBAL_ENABLED
namespace {

struct State {
  bool on = false;   // PWM 출력 중
  bool raw = false;  // 보정용 펄스 모드
  float pan = 0, tilt = 0;  // 지금 보내는 각도
  float targetPan = 0, targetTilt = 0;
  int rawPanUs = 1500, rawTiltUs = 1500;
  uint32_t movedAtMs = 0;
};

State g_s;
portMUX_TYPE g_mux = portMUX_INITIALIZER_UNLOCKED;
bool g_taskStarted = false;

const float kPeriodUs = 1000000.0f / GIMBAL_PWM_HZ;
const float kMaxDuty = (float)((1UL << GIMBAL_PWM_BITS) - 1);

State snapshot() {
  portENTER_CRITICAL(&g_mux);
  State s = g_s;
  portEXIT_CRITICAL(&g_mux);
  return s;
}

uint32_t dutyFor(float us) { return (uint32_t)lroundf(us * kMaxDuty / kPeriodUs); }
float panUs(float deg) {
  return angleToUs(deg, GIMBAL_PAN_CENTER_US, GIMBAL_PAN_US_PER_DEG, GIMBAL_US_MIN, GIMBAL_US_MAX);
}
float tiltUs(float deg) {
  return angleToUs(deg, GIMBAL_TILT_DOWN_US, GIMBAL_TILT_US_PER_DEG, GIMBAL_US_MIN, GIMBAL_US_MAX);
}

bool attach() {
  // arduino-esp32 2.0.x: 채널 2·3 → LEDC 타이머 1. 카메라 XCLK(타이머 0)와 겹치지 않는다.
  if (!ledcSetup(GIMBAL_PAN_LEDC_CH, GIMBAL_PWM_HZ, GIMBAL_PWM_BITS)) return false;
  if (!ledcSetup(GIMBAL_TILT_LEDC_CH, GIMBAL_PWM_HZ, GIMBAL_PWM_BITS)) return false;
  ledcAttachPin(GIMBAL_PAN_PIN, GIMBAL_PAN_LEDC_CH);
  ledcAttachPin(GIMBAL_TILT_PIN, GIMBAL_TILT_LEDC_CH);
  return true;
}

void writeUs(float pu, float tu) {
  ledcWrite(GIMBAL_PAN_LEDC_CH, dutyFor(pu));
  ledcWrite(GIMBAL_TILT_LEDC_CH, dutyFor(tu));
}

void task(void *) {
  const float step = GIMBAL_SLEW_DEG_S / GIMBAL_UPDATE_HZ;
  TickType_t wake = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&wake, pdMS_TO_TICKS(1000 / GIMBAL_UPDATE_HZ));
    State s = snapshot();
    if (!s.on) continue;
    if (s.raw) {
      writeUs(s.rawPanUs, s.rawTiltUs);
      continue;
    }
    float np = approach(s.pan, s.targetPan, step), nt = approach(s.tilt, s.targetTilt, step);
    if (np != s.pan || nt != s.tilt) {
      portENTER_CRITICAL(&g_mux);
      g_s.pan = np;
      g_s.tilt = nt;
      g_s.movedAtMs = millis();
      portEXIT_CRITICAL(&g_mux);
    }
    writeUs(panUs(np), tiltUs(nt));
  }
}

// 꺼져 있었으면 PWM을 다시 붙인다
bool ensureOn() {
  if (snapshot().on) return true;
  if (!attach()) return false;
  portENTER_CRITICAL(&g_mux);
  g_s.on = true;
  portEXIT_CRITICAL(&g_mux);
  return true;
}

}  // namespace

bool gimbalBegin() {
  if (!attach()) return false;
  portENTER_CRITICAL(&g_mux);
  g_s = State();
  g_s.on = true;
  g_s.movedAtMs = millis();
  portEXIT_CRITICAL(&g_mux);
  writeUs(panUs(0), tiltUs(0));  // 켜지자마자 기본 자세 (서보는 첫 펄스 위치로 바로 간다)
  if (!g_taskStarted) {
    g_taskStarted = true;
    xTaskCreatePinnedToCore(task, "gimbal", 3072, nullptr, 2, nullptr, 0);
  }
  return true;
}

GimbalPose gimbalPose() {
  State s = snapshot();
  bool known = s.on && !s.raw;
  bool still = s.pan == s.targetPan && s.tilt == s.targetTilt;
  return {s.pan, s.tilt, known, known && still && (millis() - s.movedAtMs) >= GIMBAL_SETTLE_MS};
}

bool gimbalSet(float panDeg, float tiltDeg) {
  panDeg = clampf(panDeg, GIMBAL_PAN_MIN_DEG, GIMBAL_PAN_MAX_DEG);
  tiltDeg = clampf(tiltDeg, GIMBAL_TILT_MIN_DEG, GIMBAL_TILT_MAX_DEG);
  State before = snapshot();
  if (!ensureOn()) return false;
  portENTER_CRITICAL(&g_mux);
  // 각도를 모르던 상태(꺼짐·보정 펄스)였다면 천천히 움직일 기준이 없으니 목표로 바로 둔다
  if (!before.on || before.raw) {
    g_s.pan = panDeg;
    g_s.tilt = tiltDeg;
  }
  g_s.raw = false;
  g_s.targetPan = panDeg;
  g_s.targetTilt = tiltDeg;
  g_s.movedAtMs = millis();
  portEXIT_CRITICAL(&g_mux);
  return true;
}

bool gimbalRawUs(int panUs_, int tiltUs_) {
  if (!ensureOn()) return false;
  portENTER_CRITICAL(&g_mux);
  g_s.raw = true;
  g_s.rawPanUs = (int)clampf(panUs_, GIMBAL_US_MIN, GIMBAL_US_MAX);
  g_s.rawTiltUs = (int)clampf(tiltUs_, GIMBAL_US_MIN, GIMBAL_US_MAX);
  g_s.movedAtMs = millis();
  portEXIT_CRITICAL(&g_mux);
  return true;
}

bool gimbalOff() {
  portENTER_CRITICAL(&g_mux);
  g_s.on = false;
  g_s.raw = false;
  portEXIT_CRITICAL(&g_mux);
  ledcDetachPin(GIMBAL_PAN_PIN);
  ledcDetachPin(GIMBAL_TILT_PIN);
  return true;
}

void gimbalJson(char *out, size_t n) {
  State s = snapshot();
  GimbalPose p = gimbalPose();
  const char *mode = !s.on ? "off" : (s.raw ? "raw_us" : "angle");
  float pu = s.raw ? s.rawPanUs : panUs(s.pan), tu = s.raw ? s.rawTiltUs : tiltUs(s.tilt);
  snprintf(out, n,
           "{\"type\":\"gimbal\",\"enabled\":true,\"mode\":\"%s\",\"pan\":%.1f,\"tilt\":%.1f,"
           "\"target_pan\":%.1f,\"target_tilt\":%.1f,\"known\":%s,\"settled\":%s,\"pan_us\":%.0f,"
           "\"tilt_us\":%.0f,\"pins\":\"D8=GPIO%d(팬) D9=GPIO%d(틸트)\"}",
           mode, s.pan, s.tilt, s.targetPan, s.targetTilt, p.known ? "true" : "false",
           p.settled ? "true" : "false", pu, tu, GIMBAL_PAN_PIN, GIMBAL_TILT_PIN);
}

#else  // 고정 장착: 카메라는 항상 바로 아래

bool gimbalBegin() { return true; }
GimbalPose gimbalPose() { return {0, 0, true, true}; }
bool gimbalSet(float, float) { return false; }
bool gimbalRawUs(int, int) { return false; }
bool gimbalOff() { return false; }
void gimbalJson(char *out, size_t n) {
  snprintf(out, n, "{\"type\":\"gimbal\",\"enabled\":false,\"pan\":0,\"tilt\":0,\"known\":true,\"settled\":true}");
}

#endif
