#include "mic.h"

#include <Arduino.h>
#include <math.h>

#include "adpcm.h"
#include "config.h"
#include "driver/i2s.h"
#include "mbedtls/base64.h"

static MicEmitFn g_emit = nullptr;
static MicEmitWaitFn g_emitWait = nullptr;
static volatile uint32_t g_recRequestMs = 0;  // 0 = 요청 없음
static volatile bool g_ok = false;

// 녹음을 ADPCM으로 줄이고 base64를 1000자씩 끊어 보낸다 (스냅샷과 같은 방식)
//   MIC_BEGIN <rate> <samples> <adpcm_bytes> <b64_len> / base64 줄들 / MIC_END
static void sendRecording(const int16_t *pcm, size_t n) {
  size_t nbytes = (n + 1) / 2;
  uint8_t *adpcm = (uint8_t *)ps_malloc(nbytes);
  size_t b64Len = 4 * ((nbytes + 2) / 3) + 1;
  uint8_t *b64 = (uint8_t *)ps_malloc(b64Len);
  if (!adpcm || !b64) {
    free(adpcm);
    free(b64);
    g_emit("{\"type\":\"cmd_error\",\"reason\":\"녹음 전송 메모리 부족\"}");
    return;
  }
  AdpcmState st;
  adpcmEncode(st, pcm, n, adpcm);
  mbedtls_base64_encode(b64, b64Len, &b64Len, adpcm, nbytes);

  char head[96];
  snprintf(head, sizeof(head), "MIC_BEGIN %d %u %u %u", MIC_RATE, (unsigned)n, (unsigned)nbytes, (unsigned)b64Len);
  bool ok = g_emitWait(head, strlen(head), 3000);
  for (size_t off = 0; ok && off < b64Len; off += 1000)
    ok = g_emitWait((const char *)b64 + off, min((size_t)1000, b64Len - off), 5000);
  g_emitWait("MIC_END", 7, 3000);
  free(adpcm);
  free(b64);
}

static float toDb(double meanSq) {
  double rms = sqrt(meanSq) / 32768.0;
  return rms > 1e-6 ? (float)(20.0 * log10(rms)) : -120.0f;
}

static void micTask(void *) {
  const int FRAMES = 256;
  static int32_t buf[FRAMES * 2];  // 32비트 스테레오 (INMP441은 한쪽 채널만 쓴다)
  int slot = -1;                   // 소리가 실린 채널: 0/1, -1 = 아직 모름
  float dcPrev = 0, dcOut = 0;
  double accSq = 0, accCh[2] = {0, 0};
  int32_t peak = 0;
  uint32_t count = 0, lastReport = millis();
  int16_t *rec = nullptr;
  size_t recN = 0, recPos = 0;

  for (;;) {
    size_t bytes = 0;
    if (i2s_read(I2S_NUM_0, buf, sizeof(buf), &bytes, pdMS_TO_TICKS(200)) != ESP_OK || bytes == 0) {
      g_ok = false;
      continue;
    }
    if (g_recRequestMs && !rec) {
      recN = (size_t)MIC_RATE * g_recRequestMs / 1000;
      recPos = 0;
      rec = (int16_t *)ps_malloc(recN * sizeof(int16_t));
      if (!rec) {
        g_recRequestMs = 0;
        g_emit("{\"type\":\"cmd_error\",\"reason\":\"녹음 메모리 부족\"}");
      }
    }

    const int frames = bytes / 8;
    for (int i = 0; i < frames; i++) {
      int32_t ch[2] = {buf[2 * i] >> 8, buf[2 * i + 1] >> 8};  // 32비트 칸의 위쪽 24비트
      accCh[0] += (double)ch[0] * ch[0];
      accCh[1] += (double)ch[1] * ch[1];
      float x = (float)ch[slot < 0 ? 0 : slot];
      dcOut = x - dcPrev + 0.995f * dcOut;  // 직류 성분 제거
      dcPrev = x;
      int32_t s = (int32_t)(dcOut * MIC_GAIN / 256.0f);  // 24비트 → 16비트 × 이득
      if (s > 32767) s = 32767;
      if (s < -32768) s = -32768;
      accSq += (double)s * s;
      if (abs(s) > peak) peak = abs(s);
      count++;
      if (rec && recPos < recN) rec[recPos++] = (int16_t)s;
    }
    if (rec && recPos >= recN) {
      sendRecording(rec, recN);
      free(rec);
      rec = nullptr;
      g_recRequestMs = 0;
    }

    uint32_t now = millis();
    if (now - lastReport >= MIC_REPORT_MS && count) {
      // 어느 채널에 소리가 실렸는지: 한쪽이 다른 쪽보다 확실히 크면 그쪽으로 정한다
      if (slot < 0 && (accCh[0] > 0 || accCh[1] > 0)) {
        if (accCh[0] > accCh[1] * 16) slot = 0;
        else if (accCh[1] > accCh[0] * 16) slot = 1;
      }
      double ch0 = accCh[0] / count / 65536.0, ch1 = accCh[1] / count / 65536.0;  // 24비트 → 16비트 척도
      g_ok = (ch0 + ch1) > 1.0;  // 전부 0이면 배선이나 전원 문제
      char line[220];
      snprintf(line, sizeof(line),
               "{\"type\":\"mic\",\"ok\":%s,\"rms_db\":%.1f,\"peak_db\":%.1f,\"slot\":%d,\"ch_db\":[%.1f,%.1f],"
               "\"recording\":%s}",
               g_ok ? "true" : "false", toDb(accSq / count), toDb((double)peak * peak), slot, toDb(ch0), toDb(ch1),
               rec ? "true" : "false");
      g_emit(line);
      accSq = accCh[0] = accCh[1] = 0;
      peak = 0;
      count = 0;
      lastReport = now;
    }
  }
}

bool micBegin(MicEmitFn emit, MicEmitWaitFn emitWait) {
  g_emit = emit;
  g_emitWait = emitWait;

  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = MIC_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;  // INMP441: 32비트 칸에 24비트 데이터
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;  // 두 채널을 다 읽고 소리 있는 쪽을 고른다
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 4;
  cfg.dma_buf_len = 256;
  if (i2s_driver_install(I2S_NUM_0, &cfg, 0, nullptr) != ESP_OK) return false;

  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = MIC_SCK_PIN;
  pins.ws_io_num = MIC_WS_PIN;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = MIC_SD_PIN;
  if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) return false;

  xTaskCreatePinnedToCore(micTask, "mic", 6144, nullptr, 2, nullptr, 0);
  return true;
}

bool micRequestRecord(uint32_t ms) {
  if (g_recRequestMs) return false;
  g_recRequestMs = ms;
  return true;
}

bool micOk() { return g_ok; }
