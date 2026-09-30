import base64
import io
from types import SimpleNamespace as NS

from PIL import Image

from crosscheck.agents.base import VMHandle, fit_for_model
from crosscheck.agents.claude import ClaudeOperator


class FakeHostd:
    def __init__(self, w=1920, h=1080):
        self.w, self.h = w, h
        self.inputs = []

    def call(self, method, **kw):
        if method == "screenshot":
            buf = io.BytesIO()
            Image.new("RGB", (self.w, self.h), (len(self.inputs) * 20 % 255, 0, 0)).save(buf, format="PNG")
            return {"png_b64": base64.b64encode(buf.getvalue()).decode(), "width": self.w, "height": self.h, "t": 0}
        if method == "input":
            from crosscheck.hostd import qmp

            for a in kw["actions"]:
                if a["action"] == "key":
                    qmp.combo_events(a["keys"])  # the real hostd validates keys the same way
            self.inputs.append(kw["actions"])
            return {"ok": True}
        raise AssertionError(method)


def tool_use(i, name, inp, toolset=True):
    return NS(type="tool_use", id=f"tu_{i}", name=name, input=inp, toolset_name="computer" if toolset else None)


class FakeMessages:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        content = self.turns.pop(0)
        return NS(
            content=content,
            stop_reason="tool_use" if content else "end_turn",
            usage=NS(input_tokens=100, output_tokens=10),
        )


def test_operator_loop_maps_actions_and_reports():
    turns = [
        [tool_use(1, "screenshot", {})],
        [
            tool_use(2, "left_click", {"coordinate": [100, 50]}),
            tool_use(3, "type", {"text": "hi"}),
            tool_use(4, "report_step", {"index": 0, "result": "met", "observation": "menu opened"}, toolset=False),
        ],
        [tool_use(5, "report_step", {"index": 1, "result": "not_met", "observation": "no dialog"}, toolset=False)],
        [],
    ]
    msgs = FakeMessages(turns)
    client = NS(messages=msgs, beta=NS(messages=msgs))
    hostd = FakeHostd(3840, 2160)
    op = ClaudeOperator(api_key=None, client=client)
    steps = [{"do": "Open menu", "expect": "menu"}, {"do": "Open dialog", "expect": "dialog"}]
    results = op.run(VMHandle(hostd, "v1"), steps, max_steps=20, deadline=10**12)
    assert [r.result for r in results] == ["met", "not_met"]
    # 4K screen is downscaled for the model; clicks are scaled back to screen space
    _, scale = fit_for_model(VMHandle(hostd, "v1").screenshot())
    click = hostd.inputs[0][0]
    assert click["action"] == "click" and click["x"] == round(100 * scale)
    # every computer tool result echoes toolset_name, report_step results do not
    second_user = msgs.requests[2]["messages"][-1]["content"]
    by_id = {c["tool_use_id"]: c for c in second_user}
    assert by_id["tu_2"]["toolset_name"] == "computer" and "toolset_name" not in by_id["tu_4"]
    assert msgs.requests[0]["tools"][0] == {"type": "computer_toolset_20260801"}
    assert "untrusted" in msgs.requests[0]["system"]
    assert op.usage["input_tokens"] == 300  # stops once every step is reported


def test_operator_failed_action_skips_rest_of_batch():
    turns = [[tool_use(1, "key", {"text": "ü"}), tool_use(2, "type", {"text": "x"})], []]
    msgs = FakeMessages(turns)
    op = ClaudeOperator(api_key=None, client=NS(messages=msgs, beta=NS(messages=msgs)))
    op.run(VMHandle(FakeHostd(), "v1"), [{"do": "x"}], max_steps=5, deadline=10**12)
    results = msgs.requests[1]["messages"][-1]["content"]
    assert results[0]["is_error"] and results[1]["content"].startswith("Not executed")
