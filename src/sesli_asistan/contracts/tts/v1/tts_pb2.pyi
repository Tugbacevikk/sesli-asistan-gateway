# ÜRETİLMİŞ DOSYA — elle değiştirmeyin. Kaynak: proto/tts/v1/tts.proto · python tools/proto/uret.py
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class SynthesizeRequest(_message.Message):
    __slots__ = ("call_id", "text", "sample_rate_hz")
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_RATE_HZ_FIELD_NUMBER: _ClassVar[int]
    call_id: str
    text: str
    sample_rate_hz: int
    def __init__(self, call_id: _Optional[str] = ..., text: _Optional[str] = ..., sample_rate_hz: _Optional[int] = ...) -> None: ...

class AudioChunk(_message.Message):
    __slots__ = ("pcm16", "sample_rate_hz", "last")
    PCM16_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_RATE_HZ_FIELD_NUMBER: _ClassVar[int]
    LAST_FIELD_NUMBER: _ClassVar[int]
    pcm16: bytes
    sample_rate_hz: int
    last: bool
    def __init__(self, pcm16: _Optional[bytes] = ..., sample_rate_hz: _Optional[int] = ..., last: _Optional[bool] = ...) -> None: ...
