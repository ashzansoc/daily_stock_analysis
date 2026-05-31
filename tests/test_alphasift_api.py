# -*- coding: utf-8 -*-
"""Tests for the minimal AlphaSift screening endpoints."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    litellm = MagicMock()

from api.v1.endpoints import alphasift as alphasift_endpoint
from src.config import Config

DEFAULT_ALPHASIFT_TEST_SPEC = (
    "git+https://github.com/ZhuLinsen/alphasift.git"
    "@2c76b2b6074ae3bae01d52e5e830a4af3e3246b2"
)


def _alphasift_unavailable() -> HTTPException:
    return HTTPException(
        status_code=424,
        detail={"error": "alphasift_unavailable", "message": "AlphaSift is unavailable"},
    )


def _raise_alphasift_unavailable() -> None:
    raise _alphasift_unavailable()


def _make_adapter_module(
    *,
    screen=None,
    list_strategies=None,
    get_status=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        screen=screen or MagicMock(return_value=[]),
        list_strategies=list_strategies or (lambda: [{"id": "dual_low", "title": "双低选股", "description": "", "tag": "价值"}]),
        get_status=get_status or (lambda: {"supported_markets": ["cn"]}),
    )


class AlphaSiftOpportunitiesApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        Config.reset_instance()

    def tearDown(self) -> None:
        Config.reset_instance()

    def _config(self, *, enabled: bool, install_spec: str = DEFAULT_ALPHASIFT_TEST_SPEC) -> Config:
        return Config(alphasift_enabled=enabled, alphasift_install_spec=install_spec)

    def _screen(self, config: Config, **kwargs):
        return alphasift_endpoint.alphasift_screen(
            alphasift_endpoint.AlphaSiftScreenRequest(**kwargs),
            config=config,
        )

    def _strategies(self, config: Config):
        return alphasift_endpoint.alphasift_strategies(config=config)

    def test_status_defaults_to_disabled(self) -> None:
        config = self._config(enabled=False)

        with patch("api.v1.endpoints.alphasift._is_alphasift_available", return_value=False):
            payload = alphasift_endpoint.alphasift_status(config=config)

        self.assertEqual(payload["enabled"], False)
        self.assertEqual(payload["install_spec_is_default"], True)
        self.assertNotIn("install_spec", payload)

    def test_status_marks_custom_install_source(self) -> None:
        config = self._config(enabled=False, install_spec="git+https://example.com/private/alphasift.git")

        with patch("api.v1.endpoints.alphasift._is_alphasift_available", return_value=False):
            payload = alphasift_endpoint.alphasift_status(config=config)

        self.assertEqual(payload["install_spec_is_default"], False)
        self.assertNotIn("install_spec", payload)

    def test_status_maps_adapter_runtime_exception_to_unavailable(self) -> None:
        config = self._config(enabled=False)
        fake_module = _make_adapter_module(
            get_status=MagicMock(side_effect=RuntimeError("get_status failed")),
        )

        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            payload = alphasift_endpoint.alphasift_status(config=config)

        self.assertFalse(payload["available"])

    def test_screen_rejects_when_disabled(self) -> None:
        config = self._config(enabled=False)

        with self.assertRaises(HTTPException) as caught:
            self._screen(config)

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["error"], "alphasift_disabled")

    def test_screen_rejects_when_alphasift_unavailable(self) -> None:
        config = self._config(enabled=True)

        with (
            patch("api.v1.endpoints.alphasift.subprocess.run") as run_mock,
            patch("api.v1.endpoints.alphasift._import_alphasift", side_effect=_raise_alphasift_unavailable),
        ):
            with self.assertRaises(HTTPException) as caught:
                self._screen(config)

        self.assertEqual(caught.exception.status_code, 424)
        payload = caught.exception.detail
        self.assertEqual(payload["error"], "alphasift_unavailable")
        self.assertIn("AlphaSift", payload["message"])
        run_mock.assert_not_called()

    def test_screen_rejects_unavailable_without_install_side_effect(self) -> None:
        config = self._config(enabled=True)
        with (
            patch("api.v1.endpoints.alphasift.subprocess.run") as run_mock,
            patch("api.v1.endpoints.alphasift._import_alphasift", side_effect=_raise_alphasift_unavailable),
        ):
            with self.assertRaises(HTTPException) as caught:
                self._screen(config, market="cn", strategy="dual_low", max_results=5)

        self.assertEqual(caught.exception.status_code, 424)
        self.assertEqual(caught.exception.detail["error"], "alphasift_unavailable")
        run_mock.assert_not_called()

    def test_install_handles_adapter_runtime_exception_during_status_check(self) -> None:
        config = self._config(enabled=True)
        fake_module = _make_adapter_module(
            screen=MagicMock(),
            list_strategies=MagicMock(return_value=[]),
            get_status=MagicMock(side_effect=RuntimeError("get_status failed")),
        )
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch(
                "api.v1.endpoints.alphasift.ALLOWED_ALPHASIFT_INSTALL_SPECS",
                new=frozenset({
                    *alphasift_endpoint.ALLOWED_ALPHASIFT_INSTALL_SPECS,
                    config.alphasift_install_spec,
                }),
            ),
            patch("api.v1.endpoints.alphasift.subprocess.run", return_value=completed) as run_mock,
            patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module),
        ):
            with self.assertRaises(HTTPException) as caught:
                alphasift_endpoint.alphasift_install(config=config)

        self.assertEqual(caught.exception.status_code, 424)
        self.assertEqual(caught.exception.detail["error"], "alphasift_unavailable")
        run_mock.assert_called_once()

    def test_install_rejects_when_disabled_without_side_effects(self) -> None:
        config = self._config(enabled=False)

        with (
            patch("api.v1.endpoints.alphasift.subprocess.run") as run_mock,
            patch("api.v1.endpoints.alphasift._import_alphasift") as import_mock,
        ):
            with self.assertRaises(HTTPException) as caught:
                alphasift_endpoint.alphasift_install(config=config)

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["error"], "alphasift_disabled")
        import_mock.assert_not_called()
        run_mock.assert_not_called()

    def test_install_invokes_pip_when_enabled_and_missing(self) -> None:
        config = self._config(enabled=True)
        fake_module = SimpleNamespace(
            screen=MagicMock(),
            list_strategies=MagicMock(return_value=[]),
            get_status=MagicMock(return_value={"supported_markets": ["cn"]}),
        )
        completed = SimpleNamespace(returncode=0, stdout="installed", stderr="")

        with (
            patch(
                "api.v1.endpoints.alphasift.ALLOWED_ALPHASIFT_INSTALL_SPECS",
                new=frozenset({
                    *alphasift_endpoint.ALLOWED_ALPHASIFT_INSTALL_SPECS,
                    config.alphasift_install_spec,
                }),
            ),
            patch("api.v1.endpoints.alphasift.subprocess.run", return_value=completed) as run_mock,
            patch(
                "api.v1.endpoints.alphasift._import_alphasift",
                side_effect=[_alphasift_unavailable(), fake_module],
            ),
        ):
            payload = alphasift_endpoint.alphasift_install(config=config)

        self.assertEqual(payload["installed"], True)
        self.assertEqual(payload["already_installed"], False)
        self.assertEqual(payload["install_spec_is_default"], True)
        self.assertNotIn("install_spec", payload)
        run_mock.assert_called_once()
        self.assertIn(DEFAULT_ALPHASIFT_TEST_SPEC, run_mock.call_args.args[0])

    def test_install_marks_custom_spec_as_non_default_without_exposing_spec(self) -> None:
        config = self._config(enabled=True, install_spec="git+https://example.com/private/alphasift.git")
        fake_module = SimpleNamespace(
            screen=MagicMock(),
            list_strategies=MagicMock(return_value=[]),
            get_status=MagicMock(return_value={"supported_markets": ["cn"]}),
        )
        completed = SimpleNamespace(returncode=0, stdout="installed", stderr="")

        with (
            patch(
                "api.v1.endpoints.alphasift.ALLOWED_ALPHASIFT_INSTALL_SPECS",
                new=frozenset({
                    *alphasift_endpoint.ALLOWED_ALPHASIFT_INSTALL_SPECS,
                    config.alphasift_install_spec,
                }),
            ),
            patch("api.v1.endpoints.alphasift.subprocess.run", return_value=completed) as run_mock,
            patch(
                "api.v1.endpoints.alphasift._import_alphasift",
                side_effect=[_alphasift_unavailable(), fake_module],
            ),
        ):
            payload = alphasift_endpoint.alphasift_install(config=config)

        self.assertEqual(payload["installed"], True)
        self.assertEqual(payload["already_installed"], False)
        self.assertEqual(payload["install_spec_is_default"], False)
        self.assertNotIn("install_spec", payload)
        run_mock.assert_called_once()
        self.assertIn(config.alphasift_install_spec, run_mock.call_args.args[0])

    def test_strategies_returns_adapter_strategies(self) -> None:
        config = self._config(enabled=True)
        fake_module = _make_adapter_module(
            list_strategies=lambda: [
                {"id": "dual_low", "title": "双低选股", "description": "value", "tag": "价值"},
                {"id": "trend_quality", "title": "趋势质量", "description": "trend", "tag": "框架"},
            ],
            get_status=lambda: {"supported_markets": ["cn"]},
        )

        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            payload = self._strategies(config=config)

        self.assertEqual(
            payload,
            {
                "strategies": [
                    {
                        "id": "dual_low",
                        "title": "双低选股",
                        "description": "value",
                        "tag": "价值",
                        "market": "",
                    },
                    {
                        "id": "trend_quality",
                        "title": "趋势质量",
                        "description": "trend",
                        "tag": "框架",
                        "market": "",
                    },
                ]
            },
        )

    def test_screen_calls_dsa_adapter_and_normalizes_nan(self) -> None:
        config = self._config(enabled=True)
        fake_module = _make_adapter_module(
            screen=MagicMock(
                return_value={
                    "picks": [
                        {
                            "code": "600519",
                            "name": "Kweichow Moutai",
                            "score": float("nan"),
                            "ranking_reason": "AlphaSift pick",
                            "nested": {
                                "pe": float("inf"),
                                "pb": float("-inf"),
                                "eps": 20.5,
                            },
                        },
                    ]
                }
            ),
            list_strategies=lambda: [{"id": "dual_low", "title": "双低选股"}],
            get_status=lambda: {"supported_markets": ["cn"]},
        )

        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            payload = self._screen(config, market="cn", strategy="dual_low", max_results=5)

        fake_module.screen.assert_called_once_with(
            "dual_low",
            market="cn",
            max_results=5,
            use_llm=False,
        )
        self.assertEqual(payload["candidate_count"], 1)
        self.assertIsNone(payload["candidates"][0]["score"])
        self.assertIsNone(payload["candidates"][0]["raw"]["score"])
        self.assertIsNone(payload["candidates"][0]["raw"]["nested"]["pe"])
        self.assertIsNone(payload["candidates"][0]["raw"]["nested"]["pb"])

    def test_screen_allows_non_listed_strategy_as_custom(self) -> None:
        config = self._config(enabled=True)
        fake_module = _make_adapter_module(
            list_strategies=lambda: [{"id": "dual_low", "title": "双低选股"}],
            get_status=lambda: {"supported_markets": ["cn"]},
            screen=MagicMock(return_value={"candidates": []}),
        )

        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            payload = self._screen(config, market="cn", strategy="custom_alpha", max_results=5)

        fake_module.screen.assert_called_once_with(
            "custom_alpha",
            market="cn",
            max_results=5,
            use_llm=False,
        )
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(payload["candidate_count"], 0)

    def test_screen_allows_custom_strategy_when_strategies_list_is_empty(self) -> None:
        config = self._config(enabled=True)
        fake_module = _make_adapter_module(
            list_strategies=lambda: [],
            get_status=lambda: {"supported_markets": ["cn"]},
            screen=MagicMock(return_value={"candidates": []}),
        )

        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            payload = self._screen(config, market="cn", strategy="fallback_custom", max_results=5)

        fake_module.screen.assert_called_once_with(
            "fallback_custom",
            market="cn",
            max_results=5,
            use_llm=False,
        )
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(payload["candidate_count"], 0)

    def test_screen_rejects_unsupported_market(self) -> None:
        config = self._config(enabled=True)
        fake_module = _make_adapter_module(
            list_strategies=lambda: [{"id": "dual_low", "title": "双低选股"}],
            get_status=lambda: {"supported_markets": ["hk", "us"]},
            screen=MagicMock(return_value=[]),
        )

        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            with self.assertRaises(HTTPException) as caught:
                self._screen(config, market="cn", strategy="dual_low", max_results=5)

        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.detail["error"], "alphasift_invalid_market")

    def test_screen_maps_user_input_exception_to_4xx(self) -> None:
        config = self._config(enabled=True)
        fake_module = _make_adapter_module(
            list_strategies=lambda: [{"id": "dual_low", "title": "双低选股"}],
            get_status=lambda: {"supported_markets": ["cn"]},
            screen=MagicMock(side_effect=ValueError("unsupported strategy")),
        )

        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            with self.assertRaises(HTTPException) as caught:
                self._screen(config, market="cn", strategy="dual_low", max_results=5)

        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.detail["error"], "alphasift_invalid_input")


if __name__ == "__main__":
    unittest.main()
