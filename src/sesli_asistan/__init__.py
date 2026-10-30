"""Santral sesli yönlendirme asistanı (yalnızca Türkçe). Mimari: docs/tasarim.md."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sesli-asistan-gateway")
except PackageNotFoundError:          # kurulmadan kaynaktan çalıştırma
    __version__ = "0+bilinmiyor"
