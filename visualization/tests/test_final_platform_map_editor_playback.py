from __future__ import annotations

import unittest
from pathlib import Path


PAGE = (
    Path(__file__).resolve().parents[1]
    / "prototype"
    / "final_platform_map_editor.html"
)


class FinalPlatformPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.page = PAGE.read_text(encoding="utf-8")

    def test_three_speed_modes_have_distinct_scheduler_contracts(self) -> None:
        for marker in (
            '<option value="fast">快速</option>',
            '<option value="standard" selected>标准</option>',
            '<option value="slow">慢速</option>',
            'fast: Object.freeze({key:"fast", label:"快速", waitAfterStepMs:0, compensateStep:false})',
            'standard: Object.freeze({key:"standard", label:"标准", waitAfterStepMs:300, compensateStep:false})',
            'slow: Object.freeze({key:"slow", label:"慢速", waitAfterStepMs:600, compensateStep:true})',
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn('<option value="150">快速</option>', self.page)
        self.assertNotIn("Math.max(100, Number($(\"speedSelect\").value)", self.page)

    def test_fast_mode_has_one_non_overlapping_auto_step_chain(self) -> None:
        scheduler = self.page[
            self.page.index("async function runAutoStep()") :
            self.page.index("function startTimer()")
        ]
        for marker in (
            "if(!autoPlaying || !activeSession || pending || sessionFinished) return;",
            "await stepOnce();",
            "if(!autoPlaying || pending || sessionFinished) return;",
            "scheduleNextStep();",
            "if(!autoPlaying || !activeSession || pending || sessionFinished || timer !== null) return;",
            "timer = setTimeout(() => {",
            "void runAutoStep();",
        ):
            self.assertIn(marker, scheduler)
        self.assertNotIn("setInterval", scheduler)
        self.assertNotIn("Promise.all([stepOnce", scheduler)

    def test_slow_mode_compensates_the_measured_step_duration(self) -> None:
        self.assertIn(
            "return profile.waitAfterStepMs + (profile.compensateStep ? measuredStep : 0);",
            self.page,
        )
        self.assertIn("lastAutoStepDurationMs = performance.now() - started;", self.page)
        self.assertIn("playbackWaitAfterStep(profile, lastAutoStepDurationMs)", self.page)
        self.assertIn("clearTimeout(timer);", self.page)
        self.assertIn("$(\"speedSelect\").onchange = reschedulePlaybackForSpeed;", self.page)

    def test_auto_playback_cannot_race_step_or_reset_controls(self) -> None:
        controls = self.page[
            self.page.index("function setRunControlsBusy(") :
            self.page.index("function resize()")
        ]
        reset_body = self.page[
            self.page.index("async function resetSession()") :
            self.page.index("function responseFilename(")
        ]
        self.assertIn(
            '$("stepBtn").disabled = busy || autoPlaying || sessionFinished;',
            controls,
        )
        self.assertIn('["initBtn", "sampleBtn"].forEach(id => { $(id).disabled = busy || autoPlaying; });', controls)
        self.assertIn("stopTimer();", reset_body)
        self.assertLess(reset_body.index("stopTimer();"), reset_body.index('await api("/api/session/reset"'))
        self.assertLess(reset_body.index("pending = true;"), reset_body.index('await api("/api/session/reset"'))

    def test_fast_mode_skips_only_visual_transition_frames(self) -> None:
        transition = self.page[
            self.page.index("function startPersonTransition(") :
            self.page.index("function mapTransform(")
        ]
        self.assertIn(
            'if(autoPlaying && selectedPlaybackSpeed().key === "fast") return;',
            transition,
        )
        self.assertLess(
            transition.index('selectedPlaybackSpeed().key === "fast"'),
            transition.index("requestAnimationFrame(animate)"),
        )

    def test_playback_diagnostics_record_wait_steps_and_throughput(self) -> None:
        for marker in (
            "const playbackDiagnostics = window.__p5PlaybackDiagnostics = {",
            'playbackDiagnostics.speed = profile.key;',
            "playbackDiagnostics.last_step_ms = lastAutoStepDurationMs;",
            "playbackDiagnostics.last_wait_ms = autoDelayMs;",
            "playbackDiagnostics.completed_steps += advanced;",
            "playbackDiagnostics.steps_per_second = playbackDiagnostics.elapsed_ms > 0",
            "节奏 ${playbackProfile.label}",
            "上步后等待 ${formatDuration(lastPlaybackWaitMs)}",
        ):
            self.assertIn(marker, self.page)


if __name__ == "__main__":
    unittest.main()
