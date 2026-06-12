import asyncio
import os
import re
import sys
import threading
import time
from collections import OrderedDict, deque

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_CACHE_TTL_SECONDS = 300
_CACHE_MAX_ENTRIES = 128
_CACHE: OrderedDict[str, tuple[float, str]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_MCP_SLOTS = threading.BoundedSemaphore(4)
_REQUEST_TIMES: deque[float] = deque()
_REQUEST_LOCK = threading.Lock()
_REQUESTS_PER_MINUTE = 30


def _claim_request_slot(now: float) -> bool:
    with _REQUEST_LOCK:
        while _REQUEST_TIMES and now - _REQUEST_TIMES[0] >= 60:
            _REQUEST_TIMES.popleft()
        if len(_REQUEST_TIMES) >= _REQUESTS_PER_MINUTE:
            return False
        _REQUEST_TIMES.append(now)
        return True


def _server_environment(region: str) -> dict[str, str]:
    allowed = (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
    )
    env = {name: os.environ[name] for name in allowed if os.environ.get(name)}
    env.update(
        {
            "DDG_SAFE_SEARCH": "MODERATE",
            "DDG_REGION": region,
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


async def _search_mcp(query: str, max_results: int, region: str) -> str:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "duckduckgo_mcp_server.server"],
        env=_server_environment(region),
    )
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with asyncio.timeout(40):
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "search",
                        arguments={
                            "query": query,
                            "max_results": max_results,
                            "region": region,
                        },
                    )
    text = "\n".join(
        block.text
        for block in result.content
        if getattr(block, "type", "") == "text" and getattr(block, "text", "")
    ).strip()
    if result.isError or not text:
        return "ERROR: the web search MCP returned no usable results"
    return text


def web_search(query: str, max_results: int = 6, region: str = "br-pt") -> str:
    normalized = " ".join(str(query or "").split())
    if not normalized:
        return "ERROR: search query is required"
    if len(normalized) > 500:
        return "ERROR: search query is too long (max 500 characters)"
    try:
        max_results = max(1, min(int(max_results or 6), 10))
    except (TypeError, ValueError):
        return "ERROR: max_results must be an integer"
    region = str(region or "br-pt").strip().lower()[:16]
    if not re.fullmatch(r"(?:[a-z]{2}-[a-z]{2}|wt-wt)", region):
        return "ERROR: invalid DuckDuckGo region code"
    cache_key = f"{region}\0{max_results}\0{normalized.casefold()}"
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached[0] > now:
            _CACHE.move_to_end(cache_key)
            return cached[1]
        if cached:
            _CACHE.pop(cache_key, None)

    if not _claim_request_slot(now):
        return "ERROR: web search rate limit reached; try again shortly"
    if not _MCP_SLOTS.acquire(timeout=2):
        return "ERROR: web search is busy; try again shortly"
    try:
        try:
            result = asyncio.run(_search_mcp(normalized, max_results, region))
        except TimeoutError:
            return "ERROR: web search timed out"
        except Exception as exc:
            return f"ERROR: web search unavailable ({type(exc).__name__})"
    finally:
        _MCP_SLOTS.release()

    if not result.startswith("ERROR:"):
        with _CACHE_LOCK:
            _CACHE[cache_key] = (now + _CACHE_TTL_SECONDS, result)
            _CACHE.move_to_end(cache_key)
            while len(_CACHE) > _CACHE_MAX_ENTRIES:
                _CACHE.popitem(last=False)
    return result
