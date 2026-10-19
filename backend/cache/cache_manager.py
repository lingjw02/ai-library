"""Local cache layers; provider usage is recorded only from actual reports."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import threading
from backend.schema.models import utc_now


class CacheEntry:
    def __init__(self, key, value, ttl_seconds=300, scope='default', manifest=None):
        self.key, self.value, self.scope = key, deepcopy(value), scope
        self.manifest = deepcopy(manifest)
        self.created_at = utc_now()
        self.expires_at = self.created_at + timedelta(seconds=ttl_seconds)

    def is_expired(self):
        return utc_now() >= self.expires_at


class CacheManager:
    def __init__(self):
        self._retrieval_cache, self._context_cache = {}, {}
        self._epochs, self._clear_epoch = {}, 0
        self._lock = threading.RLock()
        self._stats = {layer: dict(lookups=0, hits=0, misses=0, invalidated=0, stale_rejections=0)
                       for layer in ('retrieval', 'context')}
        self._provider_cache_stats = {'reports': 0, 'cached_tokens': None}

    @staticmethod
    def key(material):
        return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':'),
                                         ensure_ascii=False, default=str).encode('utf-8')).hexdigest()

    def generation(self, scope):
        with self._lock:
            return self._clear_epoch, self._epochs.get(scope, 0)

    def _get(self, layer, key, manifest):
        with self._lock:
            stats = self._stats[layer]
            stats['lookups'] += 1
            cache = self._retrieval_cache if layer == 'retrieval' else self._context_cache
            entry = cache.get(key)
            if entry and (entry.is_expired() or (manifest is not None and manifest != entry.manifest)):
                del cache[key]
                stats['invalidated'] += 1
                stats['stale_rejections'] += 1
                entry = None
            stats['hits' if entry else 'misses'] += 1
            return deepcopy(entry.value) if entry else None

    def _put(self, layer, key, value, scope, ttl, manifest, expected_generation):
        with self._lock:
            if expected_generation is not None and self.generation(scope) != expected_generation:
                self._stats[layer]['stale_rejections'] += 1
                return False
            cache = self._retrieval_cache if layer == 'retrieval' else self._context_cache
            cache[key] = CacheEntry(key, value, ttl, scope, manifest)
            return True

    def get_retrieval(self, key, *, manifest=None):
        return self._get('retrieval', key, manifest)

    def put_retrieval(self, key, candidate_ids, scope='default', ttl=300, *, manifest=None, expected_generation=None):
        return self._put('retrieval', key, candidate_ids, scope, ttl, manifest, expected_generation)

    def get_context_artifact(self, key, *, manifest=None):
        return self._get('context', key, manifest)

    def put_context_artifact(self, key, artifact, scope='default', ttl=300, *, manifest=None, expected_generation=None):
        return self._put('context', key, artifact, scope, ttl, manifest, expected_generation)

    def invalidate_scope(self, scope):
        with self._lock:
            self._epochs[scope] = self._epochs.get(scope, 0) + 1
            count = 0
            for layer, cache in [('retrieval', self._retrieval_cache), ('context', self._context_cache)]:
                keys = [key for key, entry in cache.items() if entry.scope == scope]
                for key in keys:
                    del cache[key]
                self._stats[layer]['invalidated'] += len(keys)
                count += len(keys)
            return count

    def clear(self):
        with self._lock:
            self._clear_epoch += 1
            for layer, cache in [('retrieval', self._retrieval_cache), ('context', self._context_cache)]:
                self._stats[layer]['invalidated'] += len(cache)
                cache.clear()

    def record_provider_usage(self, *, cached_tokens=None):
        if cached_tokens is not None and (type(cached_tokens) is not int or cached_tokens < 0):
            raise ValueError('cached_tokens must be a nonnegative provider-reported integer or None')
        with self._lock:
            self._provider_cache_stats['reports'] += 1
            self._provider_cache_stats['cached_tokens'] = cached_tokens

    def stats(self):
        with self._lock:
            result = deepcopy(self._stats)
            for layer, cache in [('retrieval', self._retrieval_cache), ('context', self._context_cache)]:
                result[layer]['entries'] = len(cache)
                result[layer]['bytes_estimate'] = sum(len(repr(entry.value).encode('utf-8')) for entry in cache.values())
            result['provider'] = deepcopy(self._provider_cache_stats)
            return result
