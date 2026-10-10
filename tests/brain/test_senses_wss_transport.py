import pytest

from memecoin_collector.wss_reference import MAX_MESSAGE_BYTES
from memecoin_collector.wss_transport import (
    FrameHeader,
    FrameProtocolError,
    FrameTooLarge,
    ServerFrame,
    WebSocketMessageAssembler,
    encode_client_frame,
    read_server_frame,
    read_server_frame_header,
)


class Reader:
    def __init__(self, data):
        self.data = data
        self.offset = 0
        self.read_sizes = []

    def read_exactly(self, size):
        self.read_sizes.append(size)
        end = self.offset + size
        value = self.data[self.offset : end]
        self.offset = end
        return value


def server_header(length, *, opcode=1, fin=True):
    first = bytes([(0x80 if fin else 0) | opcode])
    if length <= 125:
        return first + bytes([length])
    if length <= 0xFFFF:
        return first + bytes([126]) + length.to_bytes(2, "big")
    return first + bytes([127]) + length.to_bytes(8, "big")


def test_oversize_header_stops_before_payload_read():
    declared = MAX_MESSAGE_BYTES + 1
    reader = Reader(server_header(declared))
    with pytest.raises(FrameTooLarge) as exc:
        read_server_frame(reader, max_payload_bytes=MAX_MESSAGE_BYTES)
    assert exc.value.header.payload_length == declared
    assert reader.read_sizes == [2, 8]
    assert reader.offset == 10


def test_frame_header_accepts_extended_length_then_reads_exact_payload():
    payload = b"x" * 126
    reader = Reader(server_header(len(payload)) + payload)
    frame = read_server_frame(reader, max_payload_bytes=126)
    assert frame.header.payload_length == 126
    assert frame.payload == payload
    assert reader.read_sizes == [2, 2, 126]


@pytest.mark.parametrize(
    "raw,reason",
    [
        (b"\x81\x80", "wss_server_frame_masked"),
        (b"\xc1\x00", "wss_frame_extension_bits_unsupported"),
        (b"\x81\x7e\x00\x7d", "wss_frame_length_noncanonical"),
        (b"\x81\x7f\x80\x00\x00\x00\x00\x00\x00\x00", "wss_frame_length_high_bit_set"),
    ],
)
def test_invalid_server_frame_headers_fail_closed(raw, reason):
    with pytest.raises(FrameProtocolError, match=reason):
        read_server_frame_header(Reader(raw), max_payload_bytes=MAX_MESSAGE_BYTES)


def test_fragmented_text_message_allows_interleaved_control_frame():
    assembler = WebSocketMessageAssembler(max_message_bytes=8)
    assert assembler.accept(ServerFrame(FrameHeader(False, 1, 3, b""), b"abc")) is None
    assert assembler.remaining_message_bytes == 5
    assert assembler.accept(ServerFrame(FrameHeader(True, 9, 2, b""), b"hi")) == ("ping", b"hi")
    assert assembler.accept(ServerFrame(FrameHeader(True, 0, 3, b""), b"def")) == (
        "text",
        b"abcdef",
    )
    assert assembler.remaining_message_bytes == 8


def test_client_frames_are_masked_and_round_trip_with_offline_key():
    payload = b"logsSubscribe"
    key = b"abcd"
    frame = encode_client_frame(1, payload, masking_key=key)
    assert frame[0] == 0x81
    assert frame[1] & 0x80
    length = frame[1] & 0x7F
    assert length == len(payload)
    actual_key = frame[2:6]
    decoded = bytes(value ^ actual_key[i % 4] for i, value in enumerate(frame[6:]))
    assert decoded == payload
