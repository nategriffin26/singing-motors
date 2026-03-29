#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "protocol_defs.h"

typedef enum {
  PROTO_DECODE_OK = 0,
  PROTO_DECODE_BAD_ARG,
  PROTO_DECODE_COBS,
  PROTO_DECODE_SHORT,
  PROTO_DECODE_MAGIC,
  PROTO_DECODE_LENGTH,
  PROTO_DECODE_CRC,
} proto_decode_status_t;

uint16_t proto_crc16_ccitt(const uint8_t *data, size_t len);

size_t proto_cobs_encode(const uint8_t *input, size_t input_len, uint8_t *output, size_t output_max);
size_t proto_cobs_decode(const uint8_t *input, size_t input_len, uint8_t *output, size_t output_max);

bool proto_frame_encode(const proto_frame_t *frame, uint8_t *output, size_t output_max, size_t *output_len);
proto_decode_status_t proto_frame_decode_packet(
  const uint8_t *packet,
  size_t packet_len,
  uint8_t *raw_scratch,
  size_t raw_scratch_len,
  proto_frame_t *frame_out
);

uint16_t proto_read_le16(const uint8_t *buf);
uint32_t proto_read_le32(const uint8_t *buf);
void proto_write_le16(uint8_t *buf, uint16_t value);
void proto_write_le32(uint8_t *buf, uint32_t value);
