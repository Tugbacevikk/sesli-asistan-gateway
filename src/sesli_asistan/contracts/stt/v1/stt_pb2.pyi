# ÜRETİLMİŞ DOSYA — elle değiştirmeyin. Kaynak: proto/stt/v1/stt.proto · python tools/proto/uret.py
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AudioCodec(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AUDIO_CODEC_UNSPECIFIED: _ClassVar[AudioCodec]
    AUDIO_CODEC_PCMA: _ClassVar[AudioCodec]
    AUDIO_CODEC_PCMU: _ClassVar[AudioCodec]
    AUDIO_CODEC_PCM16: _ClassVar[AudioCodec]
AUDIO_CODEC_UNSPECIFIED: AudioCodec
AUDIO_CODEC_PCMA: AudioCodec
AUDIO_CODEC_PCMU: AudioCodec
AUDIO_CODEC_PCM16: AudioCodec

class AudioIn(_message.Message):
    __slots__ = ("config", "audio")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    AUDIO_FIELD_NUMBER: _ClassVar[int]
    config: StreamConfig
    audio: bytes
    def __init__(self, config: _Optional[_Union[StreamConfig, _Mapping]] = ..., audio: _Optional[bytes] = ...) -> None: ...

class StreamConfig(_message.Message):
    __slots__ = ("call_id", "codec", "sample_rate_hz", "hotwords")
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    CODEC_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_RATE_HZ_FIELD_NUMBER: _ClassVar[int]
    HOTWORDS_FIELD_NUMBER: _ClassVar[int]
    call_id: str
    codec: AudioCodec
    sample_rate_hz: int
    hotwords: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, call_id: _Optional[str] = ..., codec: _Optional[_Union[AudioCodec, str]] = ..., sample_rate_hz: _Optional[int] = ..., hotwords: _Optional[_Iterable[str]] = ...) -> None: ...

class SttEvent(_message.Message):
    __slots__ = ("type", "text", "confidence", "audio_start_ms", "audio_end_ms")
    class Type(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        TYPE_UNSPECIFIED: _ClassVar[SttEvent.Type]
        SPEECH_STARTED: _ClassVar[SttEvent.Type]
        PARTIAL: _ClassVar[SttEvent.Type]
        FINAL: _ClassVar[SttEvent.Type]
    TYPE_UNSPECIFIED: SttEvent.Type
    SPEECH_STARTED: SttEvent.Type
    PARTIAL: SttEvent.Type
    FINAL: SttEvent.Type
    TYPE_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    AUDIO_START_MS_FIELD_NUMBER: _ClassVar[int]
    AUDIO_END_MS_FIELD_NUMBER: _ClassVar[int]
    type: SttEvent.Type
    text: str
    confidence: float
    audio_start_ms: int
    audio_end_ms: int
    def __init__(self, type: _Optional[_Union[SttEvent.Type, str]] = ..., text: _Optional[str] = ..., confidence: _Optional[float] = ..., audio_start_ms: _Optional[int] = ..., audio_end_ms: _Optional[int] = ...) -> None: ...
