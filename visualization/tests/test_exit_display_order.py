from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PAGE_PATH = Path("visualization/prototype/final_platform_map_editor.html")
RESULT_PATTERN = re.compile(
    r'<pre id="exit-order-result">(.*?)</pre>', re.DOTALL
)


class ExitDisplayOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.page = PAGE_PATH.read_text(encoding="utf-8")

    def test_exit_order_is_wired_to_numeric_sort_and_existing_display_name(self) -> None:
        self.assertIn("function exitEntityOrdinal(exitId)", self.page)
        self.assertIn("function sortedActualExitEntries(distribution)", self.page)
        self.assertIn("return leftOrdinal - rightOrdinal;", self.page)
        self.assertIn(
            "const values = sortedActualExitEntries(distribution);", self.page
        )
        self.assertIn("label.textContent = exitDisplayName(name);", self.page)
        self.assertIn("label.title = String(name);", self.page)
        self.assertIn("const percent = Number(count) / total * 100;", self.page)
        self.assertIn("if(total <= 0){", self.page)
        self.assertIn("grid-template-columns:320px minmax(560px,1fr) 368px", self.page)

    def test_exit_order_rendering_in_javascript(self) -> None:
        edge = self._edge_path()
        if edge is None:
            self.skipTest("Microsoft Edge is unavailable for the JavaScript regression test")

        helper_source = self._between(
            "  function chineseExitOrdinal",
            "  function displayedRunMs",
        )
        render_source = self._between(
            "  function renderActualExitDistribution",
            "\n  function metricText",
        )
        script_start = self.page.index("<script>") + len("<script>")
        script_source = self.page[script_start : self.page.index("</script>")]
        cases = [
            {
                "name": "shuffled",
                "distribution": {
                    "exit_entity_8": 7,
                    "exit_entity_3": 4,
                    "exit_entity_1": 3,
                },
            },
            {
                "name": "complete",
                "distribution": {
                    f"exit_entity_{number}": number for number in range(8, 0, -1)
                },
            },
            {
                "name": "two_digits",
                "distribution": {
                    "exit_entity_10": 5,
                    "exit_entity_2": 2,
                },
            },
            {
                "name": "unused",
                "distribution": {
                    "exit_entity_8": 0,
                    "exit_entity_1": 2,
                },
            },
        ]
        harness = f"""<!doctype html>
<meta charset="utf-8">
<body>
<pre id="exit-order-result">pending</pre>
<script>
(() => {{
  const realDocument = window.document;
  const fullPageScript = {json.dumps(script_source, ensure_ascii=False)};
  let syntaxOk = true;
  let syntaxError = "";
  try {{
    new Function(fullPageScript);
  }} catch (error) {{
    syntaxOk = false;
    syntaxError = String(error);
  }}

  const makeNode = (tag = "div") => ({{
    tagName: tag,
    children: [],
    className: "",
    style: {{}},
    textContent: "",
    title: "",
    append(...children) {{ this.children.push(...children); }},
    appendChild(child) {{ this.children.push(child); }},
    replaceChildren(...children) {{
      this.children = children;
      this.textContent = "";
    }}
  }});

  const elements = {{
    exitBars: makeNode(),
    actualExitStatus: makeNode()
  }};
  const document = {{ createElement: makeNode }};
  const snapshot = {{}};
  let exitDisplayMapSource = null;
  let exitDisplayMap = new Map();
  function $(id) {{ return elements[id]; }}

{helper_source}

{render_source}

  function renderAndCapture(distribution) {{
    const before = JSON.stringify(distribution);
    renderActualExitDistribution(distribution, "entity");
    return {{
      sourceUnchanged: before === JSON.stringify(distribution),
      status: elements.actualExitStatus.textContent,
      rows: elements.exitBars.children.map(row => {{
        const [label, track, value] = row.children;
        return {{
          id: label.title,
          name: label.textContent,
          width: track.children[0].style.width,
          value: value.textContent
        }};
      }})
    }};
  }}

  const cases = {json.dumps(cases, ensure_ascii=False)};
  const results = Object.fromEntries(
    cases.map(testCase => [testCase.name, renderAndCapture(testCase.distribution)])
  );
  const repeated = [0, 1, 2].map(() => renderAndCapture(cases[0].distribution));
  renderActualExitDistribution({{}}, "entity");
  const emptyEntityText = elements.exitBars.textContent;
  const emptyStatus = elements.actualExitStatus.textContent;

  realDocument.getElementById("exit-order-result").textContent = JSON.stringify({{
    syntaxOk,
    syntaxError,
    results,
    repeated,
    emptyEntityText,
    emptyStatus
  }});
}})();
</script>
</body>
"""

        with tempfile.TemporaryDirectory(prefix="exit-order-test-") as temp_dir:
            harness_path = Path(temp_dir) / "exit_order_test.html"
            harness_path.write_text(harness, encoding="utf-8")
            profile_path = Path(temp_dir) / "edge-profile"
            completed = subprocess.run(
                [
                    edge,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-gpu-sandbox",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--no-first-run",
                    f"--user-data-dir={profile_path}",
                    "--dump-dom",
                    harness_path.as_uri(),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=False,
            )

        match = RESULT_PATTERN.search(completed.stdout)
        self.assertIsNotNone(
            match,
            f"Browser did not return test results. stderr={completed.stderr}",
        )
        result = json.loads(html.unescape(match.group(1)))
        self.assertTrue(result["syntaxOk"], result["syntaxError"])

        shuffled = result["results"]["shuffled"]
        self.assertTrue(shuffled["sourceUnchanged"])
        self.assertEqual(
            [row["id"] for row in shuffled["rows"]],
            ["exit_entity_1", "exit_entity_3", "exit_entity_8"],
        )
        self.assertEqual(
            [row["name"] for row in shuffled["rows"]],
            ["\u51fa\u53e3\u4e00", "\u51fa\u53e3\u4e09", "\u51fa\u53e3\u516b"],
        )
        self.assertEqual(
            [row["value"] for row in shuffled["rows"]],
            [
                "3\u4eba \u00b7 21.4%",
                "4\u4eba \u00b7 28.6%",
                "7\u4eba \u00b7 50.0%",
            ],
        )
        self.assertEqual(
            [row["width"] for row in shuffled["rows"]],
            ["21.428571428571427%", "28.57142857142857%", "50%"],
        )

        complete = result["results"]["complete"]
        self.assertEqual(
            [row["id"] for row in complete["rows"]],
            [f"exit_entity_{number}" for number in range(1, 9)],
        )
        self.assertEqual(
            [row["name"] for row in complete["rows"]],
            [
                "\u51fa\u53e3\u4e00",
                "\u51fa\u53e3\u4e8c",
                "\u51fa\u53e3\u4e09",
                "\u51fa\u53e3\u56db",
                "\u51fa\u53e3\u4e94",
                "\u51fa\u53e3\u516d",
                "\u51fa\u53e3\u4e03",
                "\u51fa\u53e3\u516b",
            ],
        )

        two_digits = result["results"]["two_digits"]
        self.assertEqual(
            [row["id"] for row in two_digits["rows"]],
            ["exit_entity_2", "exit_entity_10"],
        )

        unused = result["results"]["unused"]
        self.assertEqual(
            [row["id"] for row in unused["rows"]],
            ["exit_entity_1", "exit_entity_8"],
        )
        self.assertEqual(
            [row["value"] for row in unused["rows"]],
            ["2\u4eba \u00b7 100.0%", "0\u4eba \u00b7 0.0%"],
        )

        repeated_orders = [
            [row["id"] for row in capture["rows"]]
            for capture in result["repeated"]
        ]
        self.assertEqual(repeated_orders, repeated_orders[:1] * len(repeated_orders))
        self.assertIn("actual_exit_entity", result["emptyEntityText"])
        self.assertTrue(result["emptyStatus"].startswith("NA"))

    def _between(self, start: str, end: str) -> str:
        start_index = self.page.index(start)
        return self.page[start_index : self.page.index(end, start_index)]

    @staticmethod
    def _edge_path() -> str | None:
        configured = os.environ.get("EDGE_PATH")
        candidates = [
            configured,
            shutil.which("msedge"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        return next(
            (candidate for candidate in candidates if candidate and Path(candidate).is_file()),
            None,
        )


if __name__ == "__main__":
    unittest.main()
