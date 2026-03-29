#include "stream_parser.h"

#include <string.h>

void stream_parser_init(stream_parser_t *parser) {
  if (parser == NULL) {
    return;
  }
  parser->len = 0u;
  parser->overflowed = 0u;
}

stream_parser_event_t stream_parser_feed(
  stream_parser_t *parser,
  uint8_t byte,
  uint8_t *packet_out,
  size_t packet_out_max,
  size_t *packet_len_out
) {
  if ((parser == NULL) || (packet_out == NULL) || (packet_len_out == NULL)) {
    return STREAM_PARSER_PACKET_DROPPED;
  }

  if (byte == 0u) {
    if (parser->len == 0u) {
      return STREAM_PARSER_NONE;
    }

    if (parser->overflowed != 0u) {
      parser->len = 0u;
      parser->overflowed = 0u;
      return STREAM_PARSER_PACKET_DROPPED;
    }

    if (parser->len > packet_out_max) {
      parser->len = 0u;
      return STREAM_PARSER_PACKET_DROPPED;
    }

    memcpy(packet_out, parser->buffer, parser->len);
    *packet_len_out = parser->len;
    parser->len = 0u;
    return STREAM_PARSER_PACKET_READY;
  }

  if (parser->overflowed != 0u) {
    return STREAM_PARSER_NONE;
  }

  if (parser->len >= sizeof(parser->buffer)) {
    parser->overflowed = 1u;
    return STREAM_PARSER_NONE;
  }

  parser->buffer[parser->len++] = byte;
  return STREAM_PARSER_NONE;
}
