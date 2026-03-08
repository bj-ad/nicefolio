import os
from cachetools import TTLCache, cached
from cachetools.keys import hashkey

# configure with env or defaults
CACHE_MAXSIZE = int(os.getenv("CACHE_MAXSIZE", 1024))
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", 300))   # default 5 min

_default_cache = TTLCache(maxsize=CACHE_MAXSIZE, ttl=CACHE_TTL)

def cache(ttl=None, maxsize=None):
    """Decorator to cache function results (dev: cachetools TTL)."""
    def _decorator(func):
        # TTLCache instance per-decorator to allow different TTLs
        _ttl = ttl if ttl is not None else CACHE_TTL
        _max = maxsize if maxsize is not None else CACHE_MAXSIZE
        cache_instance = TTLCache(maxsize=_max, ttl=_ttl)
        return cached(cache_instance, key=lambda *args, **kwargs: hashkey(args, frozenset(kwargs.items())))(func)
    return _decorator

# simple helpers
def clear_default_cache():
    _default_cache.clear()