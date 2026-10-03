from __future__ import annotations

import unittest
from pathlib import Path


class PlatformLauncherTests(unittest.TestCase):
    def test_windows_entrypoint_delegates_to_the_python_launcher(self) -> None:
        batch_path = Path("start_platform.bat")
        batch = batch_path.read_text(encoding="utf-8")
        self.assertIn("experiments.platform_launcher", batch)
        self.assertIn('cd /d "%~dp0"', batch)
        self.assertIn("Platform did not start", batch)
        self.assertNotIn(b"\n", batch_path.read_bytes().replace(b"\r\n", b""))

    def test_launcher_keeps_safe_update_and_local_runtime_contracts(self) -> None:
        source = Path("experiments/platform_launcher.py").read_text(encoding="utf-8")
        self.assertIn('"status", "--porcelain", "--untracked-files=no"', source)
        self.assertIn('branch != "main"', source)
        self.assertIn('"merge", "--ff-only", "origin/main"', source)
        self.assertIn("HEALTH_URL", source)
        self.assertIn("PAGE_URL", source)
        self.assertIn("不会覆盖、暂存或藏起", source)


if __name__ == "__main__":
    unittest.main()
