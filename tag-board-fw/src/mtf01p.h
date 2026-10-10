#pragma once
// MicoAir MTF-01P 파서 — 센서 출력 설정에 따라 두 형식 중 하나가 들어온다
//  1) Micolink (기본): 0xEF | dev_id | sys_id | msg_id | seq | len | payload[len] | checksum(바이트 합)
//     msg_id 0x51 (RANGE_SENSOR) payload 20바이트, flow_vel은 "1m 높이 기준 cm/s"
//  2) MSP v2 (INAV용 설정): '$' 'X' '<' | flag | cmd(LE16) | size(LE16) | payload | crc8_dvb_s2
//     0x1F01 거리 {quality u8, distanceMm i32}, 0x1F02 플로우 {quality u8, motionX i32, motionY i32}
//     플로우 값은 메시지 사이에 쌓인 이동량. INAV와 같이 rate(°/s) = motion ÷ 간격(s) ÷ opflow_scale
#include <math.h>
#include <stdint.h>
#include <string.h>

struct MtfSample {
  uint32_t time_ms;
  uint32_t distance_mm;   // 0 = 측정 불가
  uint8_t strength;
  uint8_t precision;
  uint8_t tof_status;     // 1 = 유효
  int16_t flow_vel_x;     // cm/s @ 1m
  int16_t flow_vel_y;
  uint8_t flow_quality;
  uint8_t flow_status;    // 1 = 유효
};

class MicolinkParser {
 public:
  static const uint8_t HEADER = 0xEF;
  static const uint8_t MSG_RANGE_SENSOR = 0x51;

  uint32_t packets = 0;
  uint32_t errors = 0;

  // 패킷 하나가 완성되면 true와 함께 out을 채운다
  bool feed(uint8_t b, MtfSample &out) {
    if (idx_ == 0 && b != HEADER) return false;
    buf_[idx_++] = b;
    if (idx_ == 6 && buf_[5] > MAX_PAYLOAD) {  // len 이상 → 재동기화
      errors++;
      idx_ = 0;
      return false;
    }
    if (idx_ < 6 || idx_ < (size_t)(6 + buf_[5] + 1)) return false;

    size_t len = buf_[5];
    uint8_t sum = 0;
    for (size_t i = 0; i < 6 + len; i++) sum += buf_[i];
    bool ok = (sum == buf_[6 + len]);
    idx_ = 0;
    if (!ok) {
      errors++;
      return false;
    }
    if (buf_[3] != MSG_RANGE_SENSOR || len < 20) return false;

    const uint8_t *p = &buf_[6];
    memcpy(&out.time_ms, p + 0, 4);
    memcpy(&out.distance_mm, p + 4, 4);
    out.strength = p[8];
    out.precision = p[9];
    out.tof_status = p[10];
    memcpy(&out.flow_vel_x, p + 12, 2);
    memcpy(&out.flow_vel_y, p + 14, 2);
    out.flow_quality = p[16];
    out.flow_status = p[17];
    packets++;
    return true;
  }

 private:
  static const size_t MAX_PAYLOAD = 64;
  uint8_t buf_[6 + MAX_PAYLOAD + 1];
  size_t idx_ = 0;
};

// ---------------------------------------------------------------------------
// MSP v2 센서 메시지 (MTF-01P 를 INAV 출력으로 설정했을 때)
// ---------------------------------------------------------------------------
inline uint8_t crc8DvbS2(uint8_t crc, uint8_t a) {
  crc ^= a;
  for (int i = 0; i < 8; i++) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0xD5) : (uint8_t)(crc << 1);
  return crc;
}

class MspSensorParser {
 public:
  static const uint16_t MSP2_SENSOR_RANGEFINDER = 0x1F01;
  static const uint16_t MSP2_SENSOR_OPTIC_FLOW = 0x1F02;
  float opflowScale = 10.5f;  // INAV opflow_scale (이 FC 설정값)

  uint32_t packets = 0;
  uint32_t errors = 0;

  // 플로우 메시지가 끝날 때마다 true (가장 최근 거리와 합쳐 out 을 채운다)
  bool feed(uint8_t b, uint32_t nowUs, MtfSample &out) {
    switch (st_) {
      case 0: st_ = (b == '$') ? 1 : 0; return false;
      case 1: st_ = (b == 'X') ? 2 : (b == '$' ? 1 : 0); return false;
      case 2: st_ = (b == '<' || b == '>') ? 3 : 0; return false;
      case 3: crc_ = crc8DvbS2(0, b); st_ = 4; return false;                            // flag
      case 4: cmd_ = b; crc_ = crc8DvbS2(crc_, b); st_ = 5; return false;
      case 5: cmd_ |= (uint16_t)b << 8; crc_ = crc8DvbS2(crc_, b); st_ = 6; return false;
      case 6: size_ = b; crc_ = crc8DvbS2(crc_, b); st_ = 7; return false;
      case 7:
        size_ |= (uint16_t)b << 8;
        crc_ = crc8DvbS2(crc_, b);
        idx_ = 0;
        if (size_ > sizeof(buf_)) { errors++; st_ = 0; return false; }
        st_ = size_ ? 8 : 9;
        return false;
      case 8:
        buf_[idx_++] = b;
        crc_ = crc8DvbS2(crc_, b);
        if (idx_ >= size_) st_ = 9;
        return false;
      default:  // 9: crc
        st_ = 0;
        if (crc_ != b) { errors++; return false; }
        return handle(nowUs, out);
    }
  }

 private:
  bool handle(uint32_t nowUs, MtfSample &out) {
    int32_t a, x, y;
    if (cmd_ == MSP2_SENSOR_RANGEFINDER && size_ >= 5) {
      memcpy(&a, buf_ + 1, 4);
      distQuality_ = buf_[0];
      distMm_ = a;
      packets++;
      return false;
    }
    if (cmd_ != MSP2_SENSOR_OPTIC_FLOW || size_ < 9) return false;
    memcpy(&x, buf_ + 1, 4);
    memcpy(&y, buf_ + 5, 4);
    packets++;
    float dt = lastFlowUs_ ? (nowUs - lastFlowUs_) / 1e6f : 0.0f;
    lastFlowUs_ = nowUs;
    // 쌓인 이동량 → 각속도(rad/s) → 1m 높이에서의 속도(cm/s). Micolink 의 flow_vel 과 같은 단위로 맞춘다.
    float k = (dt > 0.001f && dt < 0.5f && opflowScale > 0.01f) ? (1.0f / dt / opflowScale) * 0.0174533f * 100.0f : 0.0f;
    out.time_ms = nowUs / 1000;
    out.distance_mm = distMm_ > 0 ? (uint32_t)distMm_ : 0;
    out.strength = distQuality_;
    out.precision = 0;
    out.tof_status = (distMm_ > 0 && distQuality_ > 0) ? 1 : 0;
    out.flow_vel_x = (int16_t)lroundf(x * k);
    out.flow_vel_y = (int16_t)lroundf(y * k);
    out.flow_quality = buf_[0];
    out.flow_status = buf_[0] > 0 ? 1 : 0;
    return true;
  }

  uint8_t st_ = 0, crc_ = 0;
  uint16_t cmd_ = 0, size_ = 0, idx_ = 0;
  uint8_t buf_[32];
  int32_t distMm_ = 0;
  uint8_t distQuality_ = 0;
  uint32_t lastFlowUs_ = 0;
};

// 두 형식을 함께 읽는다. 먼저 정상 패킷이 나온 쪽을 proto 로 보고한다.
class MtfReader {
 public:
  MicolinkParser mico;
  MspSensorParser msp;
  const char *proto = "none";

  // 모든 바이트를 두 파서에 다 넣는다 (한쪽이 먹어 버리면 다른 쪽 프레임이 깨진다)
  bool feed(uint8_t b, uint32_t nowUs, MtfSample &out) {
    MtfSample m;
    bool a = mico.feed(b, out);
    bool c = msp.feed(b, nowUs, m);
    if (a) { proto = "micolink"; return true; }
    if (c) { out = m; proto = "msp"; return true; }
    return false;
  }
  uint32_t packets() const { return mico.packets + msp.packets; }
  // 쓰지 않는 형식의 파서는 상대 데이터를 잘못 읽어 오류가 쌓이므로, 사용 중인 쪽만 센다
  uint32_t errors() const {
    if (proto[0] == 'm' && proto[1] == 'i') return mico.errors;
    if (proto[0] == 'm') return msp.errors;
    return mico.errors + msp.errors;
  }
};
