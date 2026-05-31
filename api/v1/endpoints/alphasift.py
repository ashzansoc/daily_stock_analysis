# -*- coding: utf-8 -*-
"""Optional AlphaSift stock screening endpoint."""

from __future__ import annotations

import importlib
import inspect
import math
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_config_dep
from src.config import Config, DEFAULT_ALPHASIFT_INSTALL_SPEC

router = APIRouter()

ALPHASIFT_DSA_ADAPTER_MODULE = "alphasift.dsa_adapter"
ALLOWED_ALPHASIFT_INSTALL_SPECS = frozenset({DEFAULT_ALPHASIFT_INSTALL_SPEC})


class AlphaSiftScreenRequest(BaseModel):
    market: str = Field("cn", min_length=1, max_length=16)
    strategy: str = Field("dual_low", min_length=1, max_length=64)
    max_results: int = Field(20, ge=1, le=100)


class AlphaSiftStrategyResponse(BaseModel):
    id: str
    title: str = ""
    description: str = ""
    tag: str = ""
    market: str = ""


@router.get("/status")
def alphasift_status(config: Config = Depends(get_config_dep)) -> Dict[str, Any]:
    return {
        "enabled": bool(config.alphasift_enabled),
        "available": _is_alphasift_available(),
        "install_spec_is_default": _is_default_alphasift_install_spec(config.alphasift_install_spec),
    }


@router.get("/strategies")
def alphasift_strategies(config: Config = Depends(get_config_dep)) -> Dict[str, Any]:
    _ensure_alphasift_enabled(config)
    return {"strategies": _list_strategies()}


@router.post("/install")
def alphasift_install(config: Config = Depends(get_config_dep)) -> Dict[str, Any]:
    _ensure_alphasift_enabled(config)
    return _install_alphasift(config)


def _install_alphasift(config: Config) -> Dict[str, Any]:
    install_spec_is_default = _is_default_alphasift_install_spec(config.alphasift_install_spec)
    if _is_alphasift_available():
        return _build_install_response(
            already_installed=True,
            install_spec_is_default=install_spec_is_default,
        )

    install_spec = _validate_install_spec(config.alphasift_install_spec)

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "install", install_spec],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=424,
            detail={"error": "alphasift_install_failed", "message": f"自动安装 AlphaSift 失败：{exc}"},
        ) from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"pip exited with code {completed.returncode}"
        raise HTTPException(
            status_code=424,
            detail={
                "error": "alphasift_install_failed",
                "message": f"自动安装 AlphaSift 失败：{detail}",
            },
        )

    importlib.invalidate_caches()
    if not _is_alphasift_available():
        raise HTTPException(
            status_code=424,
            detail={"error": "alphasift_unavailable", "message": "AlphaSift 安装完成，但当前进程仍无法导入 alphasift。请重启后端后重试。"},
        )

    return _build_install_response(
        already_installed=False,
        install_spec_is_default=_is_default_alphasift_install_spec(install_spec),
    )


def _validate_install_spec(raw_install_spec: str) -> str:
    install_spec = (raw_install_spec or "").strip()
    if not install_spec or install_spec.lower() == "alphasift":
        raise HTTPException(
            status_code=424,
            detail={
                "error": "alphasift_install_spec_missing",
                "message": f"请先将 ALPHASIFT_INSTALL_SPEC 配置为受信任来源：{DEFAULT_ALPHASIFT_INSTALL_SPEC}。",
            },
        )

    if install_spec not in ALLOWED_ALPHASIFT_INSTALL_SPECS:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "alphasift_install_spec_not_allowed",
                "message": (
                    "出于安全考虑，自动安装 AlphaSift 仅允许使用受信任来源："
                    f"{DEFAULT_ALPHASIFT_INSTALL_SPEC}。如需使用本地路径或 wheel，请先手动安装到当前 Python 环境。"
                ),
            },
        )

    return install_spec


@router.post("/screen")
def alphasift_screen(
    request: AlphaSiftScreenRequest,
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    _ensure_alphasift_enabled(config)

    _ensure_supported_market(request.market)
    _ensure_supported_strategy(request.strategy)

    adapter = _import_alphasift()
    screen = _get_adapter_callable(adapter, "screen", "alphasift.dsa_adapter.screen 不可调用。")
    try:
        raw = _call_alphasift_screen(screen, request.strategy, request.market, request.max_results)
    except HTTPException as exc:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "alphasift_invalid_input", "message": f"AlphaSift 参数非法：{exc}"},
        ) from exc

    candidates = _normalize_candidates(raw)
    return {
        "enabled": True,
        "candidates": candidates[: request.max_results],
        "candidate_count": len(candidates[: request.max_results]),
    }


def _ensure_alphasift_enabled(config: Config) -> None:
    if not config.alphasift_enabled:
        raise HTTPException(
            status_code=403,
            detail={"error": "alphasift_disabled", "message": "ALPHASIFT_ENABLED is false."},
        )


def _is_alphasift_available() -> bool:
    try:
        _call_alphasift_status()
        return True
    except Exception:
        return False


def _import_alphasift() -> Any:
    try:
        return importlib.import_module(ALPHASIFT_DSA_ADAPTER_MODULE)
    except Exception as exc:
        raise HTTPException(
            status_code=424,
            detail={
                "error": "alphasift_unavailable",
                "message": f"AlphaSift 未安装或未挂载到当前 Python 环境，无法导入 {ALPHASIFT_DSA_ADAPTER_MODULE}：{exc}",
            },
        ) from exc


def _get_adapter_callable(adapter: Any, name: str, missing_error: str) -> Any:
    callable_obj = getattr(adapter, name, None)
    if not callable(callable_obj):
        raise HTTPException(
            status_code=424,
            detail={"error": "alphasift_unavailable", "message": f"已导入 alphasift 适配层，但 {missing_error}"},
        )
    return callable_obj


def _call_alphasift_status() -> Dict[str, Any]:
    adapter = _import_alphasift()
    get_status = _get_adapter_callable(adapter, "get_status", "get_status() 不可调用。")
    try:
        result = _to_plain(get_status())
    except Exception as exc:
        raise HTTPException(
            status_code=424,
            detail={
                "error": "alphasift_unavailable",
                "message": f"AlphaSift 适配层 get_status 调用失败：{exc}",
            },
        ) from exc
    if not isinstance(result, dict):
        return {}
    return result


def _list_strategies() -> List[Dict[str, Any]]:
    adapter = _import_alphasift()
    list_strategies = _get_adapter_callable(adapter, "list_strategies", "list_strategies() 不可调用。")
    raw = list_strategies()
    if not isinstance(raw, list):
        raise HTTPException(
            status_code=424,
            detail={"error": "alphasift_invalid_result", "message": "AlphaSift list_strategies 返回非列表。"},
        )

    normalized: List[Dict[str, Any]] = []
    for item in raw:
        strategy = _normalize_strategy(item)
        if not strategy.get("id"):
            continue
        normalized.append(strategy)
    return normalized


def _normalize_strategy(raw: Any) -> Dict[str, Any]:
    item = _to_plain(raw)
    if isinstance(item, str):
        return AlphaSiftStrategyResponse(id=item).dict()
    if not isinstance(item, dict):
        return AlphaSiftStrategyResponse(id=str(item)).dict()

    normalized = AlphaSiftStrategyResponse(
        id=str(
            item.get("id")
            or item.get("strategy")
            or item.get("strategy_id")
            or item.get("name")
            or "",
        ),
        title=str(item.get("name") or item.get("title") or item.get("id") or ""),
        description=str(item.get("description") or ""),
        tag=str(item.get("tag") or item.get("category") or ""),
        market=str(item.get("market") or item.get("market_id") or ""),
    )
    try:
        return normalized.model_dump()
    except AttributeError:
        return normalized.dict()


def _ensure_supported_strategy(strategy: str) -> None:
    strategies = _list_strategies()
    if not strategies:
        return

    ids = {item.get("id") for item in strategies if item.get("id")}
    if strategy in ids:
        return

    # 兼容“策略列表为空时手动输入”以及“用户手动覆盖策略参数”场景，
    # 策略由适配层进行最终校验，因此在列表外仍保持透传。


def _call_alphasift_screen(screen: Any, strategy: str, market: str, max_results: int) -> Any:
    signature = inspect.signature(screen)
    params = signature.parameters
    supports_var_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in params.values())
    positional_params = [
        parameter
        for parameter in params.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    supports_var_positional = any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in params.values()
    )

    supports_max_results = "max_results" in params or supports_var_kwargs
    supports_max_output = "max_output" in params or supports_var_kwargs
    supports_use_llm = "use_llm" in params or supports_var_kwargs

    kwargs: Dict[str, Any] = {"market": market}
    if supports_max_results:
        kwargs["max_results"] = max_results
    elif supports_max_output:
        kwargs["max_output"] = max_results
    else:
        kwargs["max_results"] = max_results

    if supports_use_llm:
        kwargs["use_llm"] = False

    try:
        return screen(strategy, **kwargs)
    except TypeError as exc:
        # 某些历史实现仅接受位置参数，进行一次降级兼容。
        message = str(exc)
        # 仅对函数签名不匹配类异常进行降级，避免将适配层运行期异常重复触发。
        if "keyword" in message and "argument" in message:
            if not (supports_var_kwargs or supports_var_positional or len(positional_params) >= 3):
                raise exc
            return screen(strategy, market, max_results)
        if "positional" in message and "given" in message:
            if not (supports_var_kwargs or supports_var_positional or len(positional_params) >= 3):
                raise exc
            return screen(strategy, market, max_results)
        if not supports_var_kwargs and not (supports_var_positional or len(positional_params) >= 3):
            raise exc
        return screen(strategy, market, max_results)


def _ensure_supported_market(market: str) -> None:
    status = _call_alphasift_status()
    supported_markets = status.get("supported_markets") or status.get("markets") or status.get("market")
    if not supported_markets:
        return

    normalized = []
    if isinstance(supported_markets, str):
        normalized = [supported_markets]
    elif isinstance(supported_markets, (list, tuple, set)):
        normalized = list(supported_markets)
    else:
        normalized = []

    if market not in normalized:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "alphasift_invalid_market",
                "message": (
                    f"市场 {market} 不在 AlphaSift 适配层支持范围内"
                    f"（支持市场：{', '.join(map(str, normalized)) or '未知'}）。"
                ),
            },
        )


def _normalize_candidates(raw: Any) -> List[Dict[str, Any]]:
    data = _to_plain(raw)
    items = data
    if isinstance(data, dict):
        for key in ("picks", "candidates", "items", "results", "stocks"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    if not isinstance(items, list):
        return []
    return [_normalize_candidate(item, index + 1) for index, item in enumerate(_remove_non_finite_json_values(items))]


def _normalize_candidate(raw: Any, rank: int) -> Dict[str, Any]:
    item = _to_plain(raw)
    item = _remove_non_finite_json_values(item)  # 保障 JSON 可序列化
    if not isinstance(item, dict):
        item = {"code": str(item)}
    score = item.get("score")
    if score is None:
        score = item.get("final_score")

    return {
        "rank": item.get("rank") or rank,
        "code": item.get("code") or item.get("symbol") or item.get("stock_code") or "",
        "name": item.get("name") or item.get("stock_name") or "",
        "score": score,
        "reason": item.get("reason") or item.get("ranking_reason") or item.get("summary") or "",
        "raw": item,
    }


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict") and callable(value.dict):
        return value.dict()
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


def _remove_non_finite_json_values(value: Any) -> Any:
    if isinstance(value, list):
        return [_remove_non_finite_json_values(item) for item in value]
    if isinstance(value, tuple):
        return [_remove_non_finite_json_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _remove_non_finite_json_values(item) for key, item in value.items()}
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _build_install_response(already_installed: bool, install_spec_is_default: bool) -> Dict[str, Any]:
    return {
        "installed": True,
        "already_installed": already_installed,
        "install_spec_is_default": install_spec_is_default,
    }


def _is_default_alphasift_install_spec(install_spec: str) -> bool:
    return (install_spec or "").strip() == DEFAULT_ALPHASIFT_INSTALL_SPEC
