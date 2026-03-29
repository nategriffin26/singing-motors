#pragma once

#include <stddef.h>
#include <stdint.h>

#include "protocol_defs.h"

typedef enum {
  STREAM_PARSER_NONE = 0,
  STREAM_PARSER_PACKET_READY,
  STREAM_PARSER_PACKET_DROPPED,
} stream_parser_event_t;

typedef struct {
  uint8_t buffer[PROTO_MAX_ENCODED_FRAME];
  size_t len;
  uint8_t overflowed;
} stream_parser_t;

void stream_parser_init(stream_parser_t *parser);
stream_parser_event_t stream_parser_feed(
  stream_parser_t *parser,
  uint8_t byte,
  uint8_t *packet_out,
  size_t packet_out_max,
  size_t *packet_len_out
);
