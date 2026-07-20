from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.app import vs_method_v2
from pipeline.app.bootstrap import bootstrap_graph


class AppV2BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_graph = sys.modules.pop("graph", None)
        self.original_vs_method = sys.modules.pop("vs_method", None)

    def tearDown(self) -> None:
        sys.modules.pop("graph", None)
        sys.modules.pop("vs_method", None)
        if self.original_graph is not None:
            sys.modules["graph"] = self.original_graph
        if self.original_vs_method is not None:
            sys.modules["vs_method"] = self.original_vs_method

    def test_bootstrap_injects_v2_module_and_thresholds(self) -> None:
        fake_graph = SimpleNamespace(vs_method=vs_method_v2)
        with (
            patch("pipeline.app.bootstrap.vs_method_v2.get_settings"),
            patch("pipeline.app.bootstrap.thresholds_from_env", return_value=(0.4, 0.6)),
            patch("pipeline.app.bootstrap.importlib.import_module", return_value=fake_graph),
        ):
            result = bootstrap_graph()
        self.assertIs(result, fake_graph)
        self.assertIs(sys.modules["vs_method"], vs_method_v2)
        self.assertEqual(result.GRADE_WEAK, 0.4)
        self.assertEqual(result.GRADE_STRONG, 0.6)

    def test_bootstrap_rejects_preloaded_v1_graph(self) -> None:
        sys.modules["graph"] = SimpleNamespace(vs_method=object())
        with (
            patch("pipeline.app.bootstrap.vs_method_v2.get_settings"),
            patch("pipeline.app.bootstrap.thresholds_from_env", return_value=(0.4, 0.6)),
        ):
            with self.assertRaises(RuntimeError):
                bootstrap_graph()


if __name__ == "__main__":
    unittest.main()
