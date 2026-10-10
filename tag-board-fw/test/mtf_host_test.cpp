// PC에서 MTF-01P 파서를 시험한다 (펌웨어와 같은 src/mtf01p.h)
//   clang++ -std=c++17 -I src test/mtf_host_test.cpp -o /tmp/mtf_test && /tmp/mtf_test
// 입력은 20261003 보드가 실제로 받은 MSP 모드 바이트(mtfraw)와, 규격대로 만든 Micolink 프레임이다.
#include <cstdio>
#include <string>
#include <vector>

#include "mtf01p.h"

static int fails = 0;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

static std::vector<uint8_t> hex(const char *h) {
  std::vector<uint8_t> v;
  for (size_t i = 0; h[i] && h[i + 1]; i += 2) v.push_back((uint8_t)std::stoi(std::string(h + i, 2), nullptr, 16));
  return v;
}

int main() {
  // 1) MSP 모드: 보드에서 실제로 받은 64바이트 (거리 0x1F01 + 플로우 0x1F02 가 번갈아 옴), 10ms 간격 가정
  auto raw = hex("24583c00021f09004200000000000000004724583c00011f050034ad060000cf"
                 "24583c00021f09004200000000000000004724583c00011f050034b30600007d");
  MtfReader r;
  MtfSample s{};
  int samples = 0;
  uint32_t t = 1000000;
  for (uint8_t b : raw) {
    if (b == '$') t += 5000;  // 메시지마다 5ms → 플로우 메시지 간격 10ms
    if (r.feed(b, t, s)) samples++;
  }
  CHECK(std::string(r.proto) == "msp");
  CHECK(r.msp.errors == 0);
  CHECK(r.msp.packets == 4);
  CHECK(samples == 2);
  CHECK(s.distance_mm == 1709);   // 06ad = 1709mm (첫 거리 메시지) → 다음 플로우에 반영
  CHECK(s.tof_status == 1 && s.strength == 0x34);
  CHECK(s.flow_quality == 0x42 && s.flow_vel_x == 0 && s.flow_vel_y == 0);
  printf("MSP: proto=%s 거리=%umm 품질=%u 플로우품질=%u 패킷=%u 오류=%u\n", r.proto, s.distance_mm, s.strength,
         s.flow_quality, r.packets(), r.errors());

  // 2) MSP 플로우 단위: motionX=105, 간격 10ms, scale 10.5 → 1000°/s ≈ 17.45rad/s → 1m에서 1745cm/s
  MspSensorParser p;
  auto frame = [](uint16_t cmd, std::vector<uint8_t> pl) {
    std::vector<uint8_t> f = {'$', 'X', '<', 0, (uint8_t)cmd, (uint8_t)(cmd >> 8), (uint8_t)pl.size(), 0};
    f.insert(f.end(), pl.begin(), pl.end());
    uint8_t c = 0;
    for (size_t i = 3; i < f.size(); i++) c = crc8DvbS2(c, f[i]);
    f.push_back(c);
    return f;
  };
  MtfSample o{};
  for (uint8_t b : frame(0x1F02, {50, 0, 0, 0, 0, 0, 0, 0, 0})) p.feed(b, 2000000, o);   // 첫 플로우: 간격 없음
  for (uint8_t b : frame(0x1F02, {50, 105, 0, 0, 0, 0xF6, 0xFF, 0xFF, 0xFF})) p.feed(b, 2010000, o);  // x=105, y=-10
  CHECK(o.flow_vel_x >= 1740 && o.flow_vel_x <= 1750);
  CHECK(o.flow_vel_y >= -167 && o.flow_vel_y <= -165);
  printf("MSP 플로우 단위: x=%d y=%d (cm/s @1m)\n", o.flow_vel_x, o.flow_vel_y);
  // 체크섬이 깨지면 버린다
  auto bad = frame(0x1F01, {52, 0xad, 0x06, 0, 0});
  bad.back() ^= 0x55;
  uint32_t e0 = p.errors;
  for (uint8_t b : bad) p.feed(b, 2020000, o);
  CHECK(p.errors == e0 + 1);

  // 3) Micolink 모드: 규격대로 만든 0x51 프레임 (거리 1730mm, flow -20/+20, 품질 62)
  std::vector<uint8_t> pl(20, 0);
  uint32_t dist = 1730;
  int16_t fx = -20, fy = 20;
  memcpy(&pl[4], &dist, 4);
  pl[10] = 1;
  memcpy(&pl[12], &fx, 2);
  memcpy(&pl[14], &fy, 2);
  pl[16] = 62;
  pl[17] = 1;
  std::vector<uint8_t> mf = {0xEF, 0x0F, 0x00, 0x51, 0x07, 20};
  mf.insert(mf.end(), pl.begin(), pl.end());
  uint8_t sum = 0;
  for (uint8_t b : mf) sum += b;
  mf.push_back(sum);
  MtfReader r2;
  MtfSample m{};
  bool got = false;
  for (uint8_t b : mf) got |= r2.feed(b, 0, m);
  CHECK(got && std::string(r2.proto) == "micolink");
  CHECK(m.distance_mm == 1730 && m.flow_vel_x == -20 && m.flow_vel_y == 20 && m.flow_quality == 62);
  printf("Micolink: proto=%s 거리=%umm flow=(%d,%d)\n", r2.proto, m.distance_mm, m.flow_vel_x, m.flow_vel_y);

  fprintf(stderr, fails ? "실패 %d건\n" : "MTF 파서 테스트 통과\n", fails);
  return fails ? 1 : 0;
}
