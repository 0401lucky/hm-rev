"""Persistent usage statistics for the hm-api dashboard."""

from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from .config import CRED_DIR

STATS_FILE = CRED_DIR / "stats.json"
RECENT_LIMIT = 80
DAILY_LIMIT = 45

_LOCK = threading.Lock()


def _empty_bucket() -> dict[str, int]:
    return {
        "requests": 0,
        "success": 0,
        "error": 0,
        "stream": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def _empty_data() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "totals": _empty_bucket(),
        "models": {},
        "daily": {},
        "recent": [],
    }


def _load_unlocked() -> dict[str, Any]:
    if not STATS_FILE.exists():
        return _empty_data()
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return _empty_data()
    if not isinstance(raw, dict):
        return _empty_data()

    data = _empty_data()
    data.update(raw)
    if not isinstance(data.get("totals"), dict):
        data["totals"] = _empty_bucket()
    if not isinstance(data.get("models"), dict):
        data["models"] = {}
    if not isinstance(data.get("daily"), dict):
        data["daily"] = {}
    if not isinstance(data.get("recent"), list):
        data["recent"] = []
    return data


def _save_unlocked(data: dict[str, Any]) -> None:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = STATS_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, STATS_FILE)


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    return 0


def extract_usage(payload: Any) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    prompt_tokens = _as_int(usage.get("prompt_tokens"))
    completion_tokens = _as_int(usage.get("completion_tokens"))
    total_tokens = _as_int(usage.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _trim_daily(daily: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(daily.keys())[-DAILY_LIMIT:]
    return {key: daily[key] for key in keys}


def _add_to_bucket(
    bucket: dict[str, int],
    *,
    success: bool,
    stream: bool,
    usage: dict[str, int],
) -> None:
    bucket["requests"] = _as_int(bucket.get("requests")) + 1
    bucket["success"] = _as_int(bucket.get("success")) + (1 if success else 0)
    bucket["error"] = _as_int(bucket.get("error")) + (0 if success else 1)
    bucket["stream"] = _as_int(bucket.get("stream")) + (1 if stream else 0)
    bucket["prompt_tokens"] = (
        _as_int(bucket.get("prompt_tokens")) + usage["prompt_tokens"]
    )
    bucket["completion_tokens"] = (
        _as_int(bucket.get("completion_tokens")) + usage["completion_tokens"]
    )
    bucket["total_tokens"] = _as_int(bucket.get("total_tokens")) + usage["total_tokens"]


def record_chat_completion(
    *,
    model: str,
    stream: bool,
    status_code: int,
    duration_ms: int,
    usage: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    date_key = now.date().isoformat()
    model_key = model.strip() if model else "unknown"
    usage_value = usage or extract_usage(None)
    success = 200 <= status_code < 400 and error is None

    with _LOCK:
        data = _load_unlocked()
        totals = data.setdefault("totals", _empty_bucket())
        models = data.setdefault("models", {})
        daily = data.setdefault("daily", {})
        recent = data.setdefault("recent", [])

        model_bucket = models.setdefault(model_key, _empty_bucket())
        day_bucket = daily.setdefault(date_key, _empty_bucket())

        _add_to_bucket(totals, success=success, stream=stream, usage=usage_value)
        _add_to_bucket(model_bucket, success=success, stream=stream, usage=usage_value)
        _add_to_bucket(day_bucket, success=success, stream=stream, usage=usage_value)

        recent.insert(
            0,
            {
                "time": now.isoformat(),
                "model": model_key,
                "status_code": status_code,
                "success": success,
                "stream": stream,
                "duration_ms": max(duration_ms, 0),
                "prompt_tokens": usage_value["prompt_tokens"],
                "completion_tokens": usage_value["completion_tokens"],
                "total_tokens": usage_value["total_tokens"],
                "usage_available": usage_value["total_tokens"] > 0,
                "error": error,
            },
        )
        data["recent"] = recent[:RECENT_LIMIT]
        data["daily"] = _trim_daily(daily)
        data["updated_at"] = now.isoformat()

        _save_unlocked(data)


def get_stats_snapshot() -> dict[str, Any]:
    with _LOCK:
        data = copy.deepcopy(_load_unlocked())

    models = data.get("models", {})
    daily = data.get("daily", {})

    model_rows = []
    if isinstance(models, dict):
        for model, bucket in models.items():
            if isinstance(bucket, dict):
                row = {"model": model}
                row.update(_empty_bucket())
                row.update(bucket)
                model_rows.append(row)

    model_rows.sort(
        key=lambda item: (
            _as_int(item.get("total_tokens")),
            _as_int(item.get("requests")),
        ),
        reverse=True,
    )

    daily_rows = []
    if isinstance(daily, dict):
        for day, bucket in sorted(daily.items()):
            if isinstance(bucket, dict):
                row = {"date": day}
                row.update(_empty_bucket())
                row.update(bucket)
                daily_rows.append(row)

    totals = _empty_bucket()
    if isinstance(data.get("totals"), dict):
        totals.update(data["totals"])

    return {
        "updated_at": data.get("updated_at"),
        "totals": totals,
        "models": model_rows[:12],
        "daily": daily_rows[-14:],
        "recent": data.get("recent", [])[:25],
    }
