# ✅ MiniMax M3 Atualizado - NVIDIA NIM

## 🎯 Mudanças Aplicadas

### ❌ REMOVIDO (Antigo - HuggingFace)
```python
"minimax-m3": ProviderConfig(
    id="minimax-m3",
    name="MiniMax M3 (HuggingFace · Novita)",
    base_url="https://router.huggingface.co/v1",
    default_model="openai/MiniMaxAI/MiniMax-M3:novita",
    api_key_env="HF_TOKEN",
    ...
)
```

### ✅ ADICIONADO (Novo - NVIDIA NIM)
```python
"nvidia-minimax": ProviderConfig(
    id="nvidia-minimax",
    name="MiniMax M3 (NVIDIA NIM)",
    base_url="https://integrate.api.nvidia.com/v1",
    default_model="minimaxai/minimax-m3",
    api_key_env="NVIDIA_MINIMAX_KEY",
    ...
)
```

---

## 📊 Comparação

| Item | Antes (HuggingFace) | Depois (NVIDIA NIM) |
|------|---------------------|---------------------|
| Provider ID | `minimax-m3` | `nvidia-minimax` |
| Base URL | `router.huggingface.co` | `integrate.api.nvidia.com` |
| Model ID | `openai/MiniMaxAI/MiniMax-M3:novita` | `minimaxai/minimax-m3` |
| API Key | `HF_TOKEN` | `NVIDIA_MINIMAX_KEY` |
| API Key Value | `hf_STby...` | `nvapi-CEdo...` |

---

## 🔧 Configuração

### API Keys
```python
_NVIDIA_DEEPSEEK_KEY = "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"
_NVIDIA_MINIMAX_KEY  = "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"
```

### Parâmetros
```python
{
    "model": "minimaxai/minimax-m3",
    "temperature": 1.0,
    "top_p": 0.95,
    "max_tokens": 8192,
    "stream": true
}
```

**Nota:** MiniMax M3 NÃO usa `reasoning_effort` (apenas DeepSeek V4 Pro)

---

## 📝 Arquivos Modificados

1. **`src/stupidex/llm/providers.py`**
   - Removido: `minimax-m3` (HuggingFace)
   - Removido: `_HF_ROUTER_BASE_URL` e `_HF_API_KEY`
   - Adicionado: `nvidia-minimax` (NVIDIA NIM)
   - Adicionado: `_NVIDIA_MINIMAX_KEY`
   - Renomeado: `nvidia-nim` → `nvidia-deepseek`
   - Atualizado: `DEFAULT_FALLBACK_ID` para `nvidia-deepseek`

2. **`src/stupidex/llm/handle_input.py`**
   - Atualizado: `_litellm_kwargs()` para suportar ambos providers
   - Atualizado: `build_context()` para usar novas chaves
   - Removido: referências a `_HF_API_KEY`

---

## 🧪 Teste

### Executar Teste
```bash
.venv\Scripts\python.exe test_minimax.py
```

### Código de Teste
```python
from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key="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"
)

completion = client.chat.completions.create(
    model="minimaxai/minimax-m3",
    messages=[{"role": "user", "content": "Say: OK"}],
    temperature=1.0,
    top_p=0.95,
    max_tokens=8192,
    stream=False
)
```

---

## ✨ Recursos

### MiniMax M3 (NVIDIA NIM)
- ✅ Multimodal (texto + visão)
- ✅ Tool calling suportado
- ✅ Streaming via SSE
- ✅ Context window: 8K tokens
- ✅ Max output: 8192 tokens

### DeepSeek V4 Pro (NVIDIA NIM)
- ✅ Raciocínio avançado
- ✅ Tool calling suportado
- ✅ Streaming via SSE
- ✅ Reasoning effort: low/medium/high
- ✅ Max output: 8192 tokens

---

## 🎯 Providers Disponíveis

### Server-side (Backend)
1. **nvidia-deepseek** - DeepSeek V4 Pro (coding)
2. **nvidia-minimax** - MiniMax M3 (multimodal)

### Browser-side (Puter.js)
3. claude-sonnet-4
4. claude-3-7-sonnet
5. claude-3-5-sonnet
6. claude-3-5-haiku
7. gpt-4o
8. gpt-4o-mini
9. gpt-4-1

---

## 📚 Referências

- **DeepSeek Docs:** https://docs.api.nvidia.com/nim/reference/deepseek-ai-deepseek-v4-pro-infer
- **MiniMax Docs:** https://docs.api.nvidia.com/nim/reference/minimaxai-minimax-m3-infer
- **NVIDIA NIM Base:** https://integrate.api.nvidia.com/v1

---

**Status:** ✅ COMPLETO - Ambos modelos configurados corretamente via NVIDIA NIM
