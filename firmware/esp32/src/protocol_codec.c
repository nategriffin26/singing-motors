#include "protocol_codec.h"

#include <string.h>

uint16_t proto_crc16_ccitt(const uint8_t *data, size_t len) {
  uint16_t crc = 0xFFFFu;
  size_t i = 0;
  while (i < len) {
    crc ^= ((uint16_t)data[i]) << 8;
    uint8_t bit = 0;
    while (bit < 8u) {
      if ((crc & 0x8000u) != 0u) {
        crc = (uint16_t)((crc << 1) ^ 0x1021u);
      } else {
        crc <<= 1;
      }
      bit++;
    }
    i++;
  }
  return crc;
}

size_t proto_cobs_encode(const uint8_t *input, size_t input_len, uint8_t *output, size_t output_max) {
  if ((input == NULL) || (output == NULL) || (output_max == 0u)) {
    return 0u;
  }

  size_t read_index = 0;
  size_t write_index = 1;
  size_t code_index = 0;
  uint8_t code = 1;

  while (read_index < input_len) {
    if (write_index >= output_max) {
      return 0u;
    }

    const uint8_t byte = input[read_index++];
    if (byte == 0u) {
      output[code_index] = code;
      code = 1;
      code_index = write_index++;
    } else {
      output[write_index++] = byte;
      code++;
      if (code == 0xFFu) {
        output[code_index] = code;
        code = 1;
        if (write_index >= output_max) {
          return 0u;
        }
        code_index = write_index++;
      }
    }
  }

  if (code_index >= output_max) {
    return 0u;
  }

  output[code_index] = code;
  return write_index;
}

size_t proto_cobs_decode(const uint8_t *input, size_t input_len, uint8_t *output, size_t output_max) {
  if ((input == NULL) || (output == NULL)) {
    return 0u;
  }

  size_t read_index = 0;
  size_t write_index = 0;

  while (read_index < input_len) {
    const uint8_t code = input[read_index++];
    if (code == 0u) {
      return 0u;
    }

    uint8_t i = 1;
    while (i < code) {
      if ((read_index >= input_len) || (write_index >= output_max)) {
        return 0u;
      }
      output[write_index++] = input[read_index++];
      i++;
    }

    if ((code != 0xFFu) && (read_index < input_len)) {
      if (write_index >= output_max) {
        return 0u;
      }
      output[write_index++] = 0u;
    }
  }

  return write_index;
}

uint16_t proto_read_le16(const uint8_t *buf) {
  return (uint16_t)(((uint16_t)buf[0]) | (((uint16_t)buf[1]) << 8));
}

uint32_t proto_read_le32(const uint8_t *buf) {
  return (uint32_t)(((uint32_t)buf[0]) | (((uint32_t)buf[1]) << 8) | (((uint32_t)buf[2]) << 16) | (((uint32_t)buf[3]) << 24));
}

void proto_write_le16(uint8_t *buf, uint16_t value) {
  buf[0] = (uint8_t)(value & 0xFFu);
  buf[1] = (uint8_t)((value >> 8) & 0xFFu);
}

void proto_write_le32(uint8_t *buf, uint32_t value) {
  buf[0] = (uint8_t)(value & 0xFFu);
  buf[1] = (uint8_t)((value >> 8) & 0xFFu);
  buf[2] = (uint8_t)((value >> 16) & 0xFFu);
  buf[3] = (uint8_t)((value >> 24) & 0xFFu);
}

bool proto_frame_encode(const proto_frame_t *frame, uint8_t *output, size_t output_max, size_t *output_len) {
  if ((frame == NULL) || (output == NULL) || (output_len == NULL)) {
    return false;
  }
  if (frame->payload_len > PROTO_MAX_PAYLOAD) {
    return false;
  }

  uint8_t raw[PROTO_MAX_RAW_FRAME];
  size_t raw_len = 0;

  raw[raw_len++] = frame->version;
  raw[raw_len++] = frame->cmd;
  proto_write_le16(&raw[raw_len], PROTO_MAGIC);
  raw_len += 2u;
  proto_write_le16(&raw[raw_len], frame->seq);
  raw_len += 2u;
  raw[raw_len++] = frame->flags;
  proto_write_le16(&raw[raw_len], frame->payload_len);
  raw_len += 2u;

  if (frame->payload_len > 0u) {
    memcpy(&raw[raw_len], frame->payload, frame->payload_len);
    raw_len += frame->payload_len;
  }

  const uint16_t crc = proto_crc16_ccitt(raw, raw_len);
  proto_write_le16(&raw[raw_len], crc);
  raw_len += 2u;

  const size_t encoded_len = proto_cobs_encode(raw, raw_len, output, output_max);
  if (encoded_len == 0u) {
    return false;
  }
  if (encoded_len + 1u > output_max) {
    return false;
  }

  output[encoded_len] = 0u;
  *output_len = encoded_len + 1u;
  return true;
}

proto_decode_status_t proto_frame_decode_packet(
  const uint8_t *packet,
  size_t packet_len,
  uint8_t *raw_scratch,
  size_t raw_scratch_len,
  proto_frame_t *frame_out
) {
  if ((packet == NULL) || (raw_scratch == NULL) || (frame_out == NULL)) {
    return PROTO_DECODE_BAD_ARG;
  }

  const size_t raw_len = proto_cobs_decode(packet, packet_len, raw_scratch, raw_scratch_len);
  if (raw_len == 0u) {
    return PROTO_DECODE_COBS;
  }

  if (raw_len < (PROTO_FRAME_HEADER_SIZE + PROTO_FRAME_CRC_SIZE)) {
    return PROTO_DECODE_SHORT;
  }

  size_t index = 0;
  const uint8_t version = raw_scratch[index++];
  const uint8_t cmd = raw_scratch[index++];
  const uint16_t magic = proto_read_le16(&raw_scratch[index]);
  index += 2u;
  const uint16_t seq = proto_read_le16(&raw_scratch[index]);
  index += 2u;
  const uint8_t flags = raw_scratch[index++];
  const uint16_t payload_len = proto_read_le16(&raw_scratch[index]);
  index += 2u;

  if (magic != PROTO_MAGIC) {
    return PROTO_DECODE_MAGIC;
  }
  if (payload_len > PROTO_MAX_PAYLOAD) {
    return PROTO_DECODE_LENGTH;
  }

  const size_t expected_raw = PROTO_FRAME_HEADER_SIZE + (size_t)payload_len + PROTO_FRAME_CRC_SIZE;
  if (raw_len != expected_raw) {
    return PROTO_DECODE_LENGTH;
  }

  const uint16_t rx_crc = proto_read_le16(&raw_scratch[raw_len - 2u]);
  const uint16_t calc_crc = proto_crc16_ccitt(raw_scratch, raw_len - 2u);
  if (rx_crc != calc_crc) {
    return PROTO_DECODE_CRC;
  }

  frame_out->version = version;
  frame_out->cmd = cmd;
  frame_out->seq = seq;
  frame_out->flags = flags;
  frame_out->payload_len = payload_len;
  if (payload_len > 0u) {
    memcpy(frame_out->payload, &raw_scratch[index], payload_len);
  }

  return PROTO_DECODE_OK;
}
