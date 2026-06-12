"""Tame litellm's noisy/broken startup and shim tiktoken on Python 3.14+.

litellm 1.83+ tries to load tiktoken encoder data eagerly; on
Python 3.14 inside a PyInstaller bundle, this raises
"Unknown encoding cl100k_base" because the encoder data files aren't
discoverable from sys._MEIPASS.

The shim returns a dummy encoding that always says "0 tokens" — this
is fine for chat (token counting is only used for cost estimation,
not for the actual request).
"""
import sys


class _DummyEncoding:
    """A tiktoken-compatible shim that always encodes to 0 tokens."""

    name = "cl100k_base"
    max_token_value = 0

    def encode(self, text, *a, **k):
        # Rough char-based estimate so the API still has a number to log.
        # 4 chars ≈ 1 token for English/code.
        return list(range(max(0, len(str(text)) // 4)))

    def decode(self, tokens, *a, **k):
        return ""

    def encode_single_token(self, token):
        return []

    def decode_single_token_bytes(self, token):
        return b""

    def special_tokens_set(self):
        return set()


def _shim_tiktoken() -> None:
    import types

    shim = types.ModuleType("tiktoken")
    enc = _DummyEncoding()
    shim.get_encoding = lambda name: enc
    shim.encoding_for_model = lambda name: enc
    shim.get_encoding_regex_pattern = lambda *a, **k: ""
    shim.list_encoding_names = lambda: ["cl100k_base"]
    shim.Encoding = _DummyEncoding
    shim.Tokenizer = _DummyEncoding

    sys.modules["tiktoken"] = shim
    # Pre-register common submodules so any `from tiktoken.X import ...` works
    for sub in ("ext", "load", "_tiktoken", "registry"):
        sys.modules[f"tiktoken.{sub}"] = shim


# Detect if we're frozen and on Python 3.14+
_FROZEN = getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")
_PY314_PLUS = sys.version_info >= (3, 14)

if _FROZEN and _PY314_PLUS:
    _shim_tiktoken()


def quiet_litellm() -> None:
    import logging
    for name in ("litellm", "litellm.litellm_core_utils"):
        logging.getLogger(name).setLevel(logging.ERROR)
    try:
        import litellm
        litellm.suppress_debug_info = True
        litellm.drop_params = True
    except Exception:
        pass


quiet_litellm()
