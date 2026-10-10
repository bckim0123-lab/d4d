// 보드 ADPCM 인코더(src/adpcm.h) → tools/voice_tool.py 해독기 왕복 시험
//   clang++ -std=c++17 -I src test/adpcm_host_test.cpp -o /tmp/adpcm_test && /tmp/adpcm_test | python3 test/check_adpcm.py
// 출력: 1줄 = 원본 샘플(쉼표), 2줄 = ADPCM hex
#include <cmath>
#include <cstdio>
#include <vector>

#include "adpcm.h"

int main() {
  const int rate = 16000, n = rate * 2;  // 2초
  std::vector<int16_t> pcm(n);
  unsigned seed = 1;
  for (int i = 0; i < n; i++) {
    double t = (double)i / rate;
    double env = 0.5 * (1 - cos(2 * M_PI * t * 1.5));  // 말소리처럼 커졌다 작아짐
    double v = env * (0.35 * sin(2 * M_PI * 220 * t) + 0.2 * sin(2 * M_PI * 660 * t) + 0.1 * sin(2 * M_PI * 1800 * t));
    seed = seed * 1103515245u + 12345u;
    v += 0.01 * ((int)((seed >> 16) & 0x7FFF) / 16384.0 - 1.0);  // 약한 잡음
    pcm[i] = (int16_t)lround(v * 32767);
  }
  std::vector<uint8_t> out((n + 1) / 2);
  AdpcmState st;
  size_t bytes = adpcmEncode(st, pcm.data(), n, out.data());
  for (int i = 0; i < n; i++) printf("%s%d", i ? "," : "", pcm[i]);
  printf("\n");
  for (size_t i = 0; i < bytes; i++) printf("%02x", out[i]);
  printf("\n");
  return 0;
}
