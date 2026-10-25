"""G hard disk guard and non-sensitive dependency/environment metadata."""
import shutil, sys, platform, json, hashlib
from importlib.metadata import distributions, version, PackageNotFoundError


def require_disk(path, min_gib=20):
    free = shutil.disk_usage(path).free;
    threshold = min_gib * 1024 ** 3
    if free < threshold: raise RuntimeError("G disk free below frozen20GiB; no next request/service")
    return {"path": str(path), "free_bytes": free, "minimum_free_bytes": threshold, "personal_quota_known": False}


def environment_snapshot():
    rows = sorted((d.metadata["Name"], d.version) for d in distributions() if d.metadata.get("Name"))
    lock = {"installed_distribution_versions": rows,
            "scope": "Installed metadata versions; no credentials or source URLs; not wheel-byte verification"}
    raw = json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()
    return {"python": sys.version, "platform": platform.platform(), "dependency_lock": lock,
            "dependency_lock_sha256": hashlib.sha256(raw).hexdigest(),
            "formal_R": 3, "pilot_R": 1, "analysis_seed": 20261011, "bootstrap_document_draws": 10000,
            "failure_policy": {"whole_round_max_retries": 1, "automatic_HTTP_retries": 0, "automatic_round_retries": 0,
                               "retry_reason_required": True, "retain_all_attempts": True},
            "full_instrumentation_overhead_measured": False}


def kv_layout(config):
    groups = []
    for g in getattr(config, "kv_cache_groups", []):
        spec = g.kv_cache_spec
        groups.append({"spec_type": type(spec).__name__,
                       "dtype": str(spec.dtype) if getattr(spec, "dtype", None) is not None else None,
                       "block_size": getattr(spec, "block_size", None),
                       "num_kv_heads": getattr(spec, "num_kv_heads", None),
                       "head_size": getattr(spec, "head_size", None),
                       "page_size_bytes": getattr(spec, "page_size_bytes", None)})
    return {"groups": groups, "num_blocks": getattr(config, "num_blocks", None),
            "actual_dtype_known": bool(groups) and all(g["dtype"] is not None for g in groups),
            "source": "native KVCacheManager resolved KVCacheConfig, not checkpoint filename or CLI auto"}


FROZEN_PREFILL_REFERENCE = {"qwen": 0.07601123100000001, "llama": 0.08472768}
