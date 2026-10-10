#pragma once
// IMA ADPCM 인코더 (16비트 → 4비트, 4:1). 마이크 녹음을 BLE(약 7KB/s)로 보내기 위해 쓴다.
// 2초 16kHz = 32,000샘플 → 16KB. 한 바이트에 두 샘플(아래 니블이 먼저).
// 각 녹음은 pred=0, index=0 에서 시작한다. 디코더는 tools/voice_tool.py.
#include <stddef.h>
#include <stdint.h>

static const int16_t ADPCM_STEP[89] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,    25,    28,
    31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,    88,    97,    107,   118,
    130,   143,   157,   173,   190,   209,   230,   253,   279,   307,   337,   371,   408,   449,   494,
    544,   598,   658,   724,   796,   876,   963,   1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,
    2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845,  8630,
    9493,  10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767};
static const int8_t ADPCM_INDEX[16] = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};

struct AdpcmState {
  int32_t pred = 0;
  int32_t index = 0;
};

inline uint8_t adpcmEncodeSample(AdpcmState &st, int16_t sample) {
  int32_t step = ADPCM_STEP[st.index];
  int32_t diff = (int32_t)sample - st.pred;
  uint8_t code = 0;
  if (diff < 0) {
    code = 8;
    diff = -diff;
  }
  int32_t vpdiff = step >> 3;
  if (diff >= step) { code |= 4; diff -= step; vpdiff += step; }
  step >>= 1;
  if (diff >= step) { code |= 2; diff -= step; vpdiff += step; }
  step >>= 1;
  if (diff >= step) { code |= 1; vpdiff += step; }
  st.pred += (code & 8) ? -vpdiff : vpdiff;
  if (st.pred > 32767) st.pred = 32767;
  if (st.pred < -32768) st.pred = -32768;
  st.index += ADPCM_INDEX[code];
  if (st.index < 0) st.index = 0;
  if (st.index > 88) st.index = 88;
  return code;
}

// n 샘플을 (n+1)/2 바이트로. 돌려주는 값 = 쓴 바이트 수
inline size_t adpcmEncode(AdpcmState &st, const int16_t *in, size_t n, uint8_t *out) {
  for (size_t i = 0; i < n; i += 2) {
    uint8_t lo = adpcmEncodeSample(st, in[i]);
    uint8_t hi = (i + 1 < n) ? adpcmEncodeSample(st, in[i + 1]) : 0;
    out[i / 2] = (uint8_t)(lo | (hi << 4));
  }
  return (n + 1) / 2;
}
