#pragma once

#include <stddef.h>
#include <stdint.h>

// FC 수신기 CH6가 결정하는 제어권. 유효한 AUTO 응답이 없으면 항상 MANUAL이 기본값이다.
enum class RcAuthority : uint8_t {
  Manual = 0,
  Auto = 1,
};

class RcAuthorityGate {
 public:
  RcAuthorityGate(uint8_t channelIndex, uint16_t autoMinUs, uint16_t autoMaxUs,
                  uint32_t timeoutMs)
      : channelIndex_(channelIndex),
        autoMinUs_(autoMinUs),
        autoMaxUs_(autoMaxUs),
        timeoutMs_(timeoutMs) {}

  void observe(const uint16_t *channels, size_t count, uint32_t nowMs) {
    if (!channels || count <= channelIndex_) {
      invalidate();
      return;
    }

    channelUs_ = channels[channelIndex_];
    updatedAtMs_ = nowMs;
    valid_ = channelUs_ >= kRcMinUs && channelUs_ <= kRcMaxUs;
    observed_ = valid_ && channelUs_ >= autoMinUs_ && channelUs_ <= autoMaxUs_
                    ? RcAuthority::Auto
                    : RcAuthority::Manual;
  }

  void invalidate() {
    valid_ = false;
    observed_ = RcAuthority::Manual;
  }

  void expire(uint32_t nowMs) {
    if (valid_ && (uint32_t)(nowMs - updatedAtMs_) > timeoutMs_) invalidate();
  }

  RcAuthority authority(uint32_t nowMs) const {
    if (!fresh(nowMs)) return RcAuthority::Manual;
    return observed_;
  }

  bool rcTxAllowed(uint32_t nowMs) const { return authority(nowMs) == RcAuthority::Auto; }

  bool fresh(uint32_t nowMs) const {
    return valid_ && (uint32_t)(nowMs - updatedAtMs_) <= timeoutMs_;
  }

  uint16_t channelUs() const { return channelUs_; }

 private:
  static constexpr uint16_t kRcMinUs = 750;
  static constexpr uint16_t kRcMaxUs = 2250;

  uint8_t channelIndex_;
  uint16_t autoMinUs_;
  uint16_t autoMaxUs_;
  uint32_t timeoutMs_;
  RcAuthority observed_ = RcAuthority::Manual;
  uint16_t channelUs_ = 0;
  uint32_t updatedAtMs_ = 0;
  bool valid_ = false;
};
