"""Bounded RFC 6455 server-frame parsing for offline WSS adapters.

This module does not open sockets, perform TLS, or hold credentials. It rejects a
declared oversize payload after reading only its frame header and before payload IO.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Protocol

from memecoin_collector.wss_reference import MAX_MESSAGE_BYTES

VALID_OPCODES = {0x0, 0x1, 0x2, 0x8, 0x9, 0xA}
CONTROL_OPCODES = {0x8, 0x9, 0xA}


class ExactReader(Protocol):
    def read_exactly(self, size: int) -> bytes: ...


class FrameProtocolError(ValueError):
    """Malformed or unsupported server frame; the connection must be sealed."""


class FrameTooLarge(ValueError):
    """Declared frame payload exceeds the remaining approved message bytes."""

    def __init__(self, header: FrameHeader):
        self.header = header
        super().__init__("wss_frame_payload_cap")


@dataclass(frozen=True)
class FrameHeader:
    fin: bool
    opcode: int
    payload_length: int
    raw: bytes


@dataclass(frozen=True)
class ServerFrame:
    header: FrameHeader
    payload: bytes


class WebSocketMessageAssembler:
    """Reassemble uncompressed text messages; surface control frames separately."""

    def __init__(self, *, max_message_bytes: int = MAX_MESSAGE_BYTES) -> None:
        if type(max_message_bytes) is not int or max_message_bytes <= 0:
            raise ValueError("wss_max_message_bytes_invalid")
        self.max_message_bytes = max_message_bytes
        self._parts: list[bytes] = []
        self._message_bytes = 0
        self._fragmented = False

    @property
    def remaining_message_bytes(self) -> int:
        return self.max_message_bytes - self._message_bytes

    def accept(self, frame: ServerFrame) -> tuple[str, bytes] | None:
        header, payload = frame.header, frame.payload
        if len(payload) != header.payload_length:
            raise FrameProtocolError("wss_frame_payload_length_mismatch")
        if header.opcode in CONTROL_OPCODES:
            if not header.fin or header.payload_length > 125:
                raise FrameProtocolError("wss_control_frame_invalid")
            return (
                "close" if header.opcode == 0x8 else "ping" if header.opcode == 0x9 else "pong",
                payload,
            )
        if header.opcode == 0x2:
            raise FrameProtocolError("wss_binary_data_unsupported")
        if header.opcode == 0x1:
            if self._fragmented:
                raise FrameProtocolError("wss_new_data_frame_during_fragment")
            self._parts = [payload]
            self._message_bytes = len(payload)
            self._fragmented = not header.fin
        elif header.opcode == 0x0:
            if not self._fragmented:
                raise FrameProtocolError("wss_continuation_without_message")
            self._parts.append(payload)
            self._message_bytes += len(payload)
            self._fragmented = not header.fin
        else:
            raise FrameProtocolError("wss_data_opcode_unsupported")
        if self._message_bytes > self.max_message_bytes:
            self.reset()
            raise FrameTooLarge(header)
        if self._fragmented:
            return None
        result = b"".join(self._parts)
        self.reset()
        return ("text", result)

    def reset(self) -> None:
        self._parts = []
        self._message_bytes = 0
        self._fragmented = False


def read_server_frame_header(reader: ExactReader, *, max_payload_bytes: int) -> FrameHeader:
    """Read and validate a server header; never read its payload on a cap failure."""
    if type(max_payload_bytes) is not int or max_payload_bytes < 0:
        raise ValueError("wss_frame_limit_invalid")
    first = reader.read_exactly(2)
    if len(first) != 2:
        raise FrameProtocolError("wss_frame_header_truncated")
    first_byte, second_byte = first
    fin = bool(first_byte & 0x80)
    if first_byte & 0x70:
        raise FrameProtocolError("wss_frame_extension_bits_unsupported")
    opcode = first_byte & 0x0F
    if opcode not in VALID_OPCODES:
        raise FrameProtocolError("wss_frame_opcode_invalid")
    if second_byte & 0x80:
        raise FrameProtocolError("wss_server_frame_masked")
    length_code = second_byte & 0x7F
    raw = bytearray(first)
    if length_code <= 125:
        payload_length = length_code
    elif length_code == 126:
        extended = reader.read_exactly(2)
        if len(extended) != 2:
            raise FrameProtocolError("wss_frame_header_truncated")
        raw.extend(extended)
        payload_length = int.from_bytes(extended, "big")
        if payload_length < 126:
            raise FrameProtocolError("wss_frame_length_noncanonical")
    else:
        extended = reader.read_exactly(8)
        if len(extended) != 8:
            raise FrameProtocolError("wss_frame_header_truncated")
        raw.extend(extended)
        if extended[0] & 0x80:
            raise FrameProtocolError("wss_frame_length_high_bit_set")
        payload_length = int.from_bytes(extended, "big")
        if payload_length <= 0xFFFF:
            raise FrameProtocolError("wss_frame_length_noncanonical")
    if opcode in CONTROL_OPCODES and (not fin or payload_length > 125):
        raise FrameProtocolError("wss_control_frame_invalid")
    header = FrameHeader(fin, opcode, payload_length, bytes(raw))
    if opcode not in CONTROL_OPCODES and payload_length > max_payload_bytes:
        raise FrameTooLarge(header)
    return header


def read_server_frame(reader: ExactReader, *, max_payload_bytes: int) -> ServerFrame:
    header = read_server_frame_header(reader, max_payload_bytes=max_payload_bytes)
    payload = reader.read_exactly(header.payload_length)
    if len(payload) != header.payload_length:
        raise FrameProtocolError("wss_frame_payload_truncated")
    return ServerFrame(header, payload)


def encode_client_frame(
    opcode: int,
    payload: bytes,
    *,
    fin: bool = True,
    masking_key: bytes | None = None,
) -> bytes:
    """Create a masked client frame; injected mask keys exist only for offline tests."""
    if opcode not in {0x0, 0x1, 0x2, 0x8, 0x9, 0xA} or not isinstance(payload, bytes):
        raise ValueError("wss_client_frame_invalid")
    if opcode in CONTROL_OPCODES and (not fin or len(payload) > 125):
        raise ValueError("wss_client_control_frame_invalid")
    key = masking_key if masking_key is not None else secrets.token_bytes(4)
    if not isinstance(key, bytes) or len(key) != 4:
        raise ValueError("wss_client_masking_key_invalid")
    length = len(payload)
    first = bytes([(0x80 if fin else 0) | opcode])
    if length <= 125:
        header = first + bytes([0x80 | length])
    elif length <= 0xFFFF:
        header = first + bytes([0x80 | 126]) + length.to_bytes(2, "big")
    else:
        header = first + bytes([0x80 | 127]) + length.to_bytes(8, "big")
    masked = bytes(value ^ key[index % 4] for index, value in enumerate(payload))
    return header + key + masked
