"""Operator without a model: executes explicit actions from the config and judges only
what can be judged mechanically (screen changed or not). Free and deterministic."""

from __future__ import annotations

import time

from .base import Operator, StepResult, VMHandle, changed_fraction


class ScriptedOperator(Operator):
    name = "none"

    def run(self, vm: VMHandle, steps: list[dict], *, max_steps: int, deadline: float) -> list[StepResult]:
        results = []
        for i, step in enumerate(steps):
            r = StepResult(index=i, instruction=step["do"])
            if time.monotonic() > deadline:
                r.result = "skipped"
                r.observation = "time limit reached"
                results.append(r)
                continue
            start = time.monotonic()
            before = vm.screenshot()
            r.frames.append(before)
            actions = step.get("actions") or []
            if actions:
                for a in actions[:max_steps]:
                    vm.act([a], measure=a.get("action") in ("click", "double_click", "key"))
                time.sleep(float(step.get("settle_s", 0.5)))
            after = vm.screenshot()
            r.frames.append(after)
            changed = changed_fraction(before.image(), after.image())
            if actions and step.get("expect_change"):
                r.result = "met" if changed > 0.002 else "not_met"
                r.observation = f"screen changed {changed:.1%} after scripted actions"
            else:
                r.result = "unclear"
                r.observation = "no model configured; step executed mechanically, expectation not judged"
            r.duration_ms = int((time.monotonic() - start) * 1000)
            results.append(r)
        return self.sanitize(results)
