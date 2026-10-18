"""Command contracts only: no runtime import, tensor load, launch or HTTP request."""

import importlib.util
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("model_launch_engines", ROOT / "scripts/model/engines.py")
engines = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = engines
SPEC.loader.exec_module(engines)


class EngineCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the actual host manifest and model metadata; never write either.
        path = ROOT / "runtime/model/deployment.json"
        if not path.is_file():
            raise unittest.SkipTest("Host deployment manifest is not part of source distributions")
        cls.manifest = json.loads(path.read_text())

    def setUp(self):
        self.config = deepcopy(self.manifest)
        self.config.update(engine="sglang", management_mode="managed", mtp=False, enable_metrics=False)

    def test_mtp_and_metrics_are_explicit_opt_ins(self):
        driver = engines.engine_for(ROOT, self.config)
        baseline = driver.launch_command()
        self.assertNotIn("--speculative-algorithm", baseline)
        self.assertNotIn("--enable-metrics", baseline)
        self.assertNotIn("--quantization", baseline)
        self.config.update(mtp=True, enable_metrics=True, context_length=131072)
        command = driver.launch_command()
        for option, value in {
            "--speculative-algorithm": "NEXTN", "--speculative-num-steps": "3",
            "--speculative-eagle-topk": "1", "--speculative-num-draft-tokens": "4",
            "--linear-attn-verify-backend": "triton", "--context-length": "131072",
            "--ple-offload-backend": "file",
        }.items():
            self.assertEqual(command[command.index(option) + 1], value)
        self.assertIn("--enable-metrics", command)
        self.assertNotIn("--speculative-draft-model-path", command)
        self.assertEqual(driver.readiness().url, f"http://{self.config['host']}:{self.config['port']}/ready")
        self.assertEqual(driver.capabilities()["abort"]["scope"], "request_id")

    def test_string_boolean_is_rejected(self):
        for field in ("mtp", "enable_metrics"):
            with self.subTest(field=field):
                config = {**self.config, field: "false"}
                with self.assertRaises(ValueError):
                    engines.engine_for(ROOT, config).launch_command()

    def test_other_engines_never_reuse_the_sglang_launch(self):
        for engine in ("vllm", "llama_cpp", "tensorrt_llm"):
            with self.subTest(engine=engine):
                config = {**self.config, "engine": engine}
                with self.assertRaises(RuntimeError):
                    engines.engine_for(ROOT, config)
                config.update(management_mode="external", external_base_url="http://127.0.0.1:30001/v1")
                driver = engines.engine_for(ROOT, config)
                with self.assertRaises(RuntimeError):
                    driver.launch_command()
                self.assertEqual(driver.readiness().model_identifier, config["served_model_name"])
                self.assertEqual(driver.readiness().url, "http://127.0.0.1:30001/v1/models")
                self.assertIsNone(driver.capabilities()["abort"])

    def test_unknown_engine_and_remote_external_endpoint_are_rejected(self):
        with self.assertRaises(ValueError):
            engines.engine_for(ROOT, {**self.config, "engine": "unknown"})
        with self.assertRaises(ValueError):
            engines.engine_for(ROOT, {
                **self.config, "engine": "vllm", "management_mode": "external",
                "external_base_url": "https://example.com/v1",
            })


if __name__ == "__main__":
    unittest.main()
