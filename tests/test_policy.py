from crosscheck.config import DEFAULT_POLICY, deep_merge, repo_config
from crosscheck.policy import AuthorFacts, PrFacts, Trigger, decide, trust_class

SHA = "a" * 40


def pr(login="alice", permission="none", merged=0, bot=False, approvals=(), labels=()):
    return PrFacts(
        repo="o/r",
        number=1,
        head_sha=SHA,
        base_sha="b" * 40,
        author=AuthorFacts(login=login, permission=permission, merged_prs=merged, is_bot=bot),
        approvals_on_head=list(approvals),
        labels=list(labels),
    )


def test_trust_classes():
    pol = deep_merge(DEFAULT_POLICY, {"classes": {"trusted": {"users": ["Friend"]}, "blocked": {"users": ["mallory"]}}})
    assert trust_class(pr(permission="write").author, pol) == "maintainer"
    assert trust_class(pr(login="friend").author, pol) == "trusted"
    assert trust_class(pr(merged=2).author, pol) == "known"
    assert trust_class(pr().author, pol) == "stranger"
    assert trust_class(pr(login="dependabot[bot]", bot=True).author, pol) == "bot"
    assert trust_class(pr(login="mallory", permission="admin").author, pol) == "blocked"


def test_default_smoke_needs_approval_for_strangers():
    d = decide(pr(), Trigger(kind="push"), DEFAULT_POLICY, None)
    assert d.run_static and not d.run_smoke
    assert any("approval" in r for r in d.reasons)
    d = decide(pr(approvals=["maint"]), Trigger(kind="approval"), DEFAULT_POLICY, None)
    assert d.run_smoke and d.approved_by == "maint"


def test_maintainer_runs_automatically():
    d = decide(pr(permission="admin"), Trigger(kind="push"), DEFAULT_POLICY, None)
    assert d.run_smoke and d.trust_class == "maintainer"


def test_mode_all_runs_everyone_but_blocked():
    pol = deep_merge(DEFAULT_POLICY, {"defaults": {"smoke": {"mode": "all"}}, "classes": {"blocked": {"users": ["x"]}}})
    assert decide(pr(), Trigger(kind="push"), pol, None).run_smoke
    d = decide(pr(login="x"), Trigger(kind="push"), pol, None)
    assert d.run_static and not d.run_smoke


def test_manual_mode_ignores_approval():
    pol = deep_merge(DEFAULT_POLICY, {"defaults": {"smoke": {"mode": "manual"}}})
    assert not decide(pr(approvals=["m"]), Trigger(kind="approval"), pol, None).run_smoke
    d = decide(
        pr(), Trigger(kind="slash-command", stage="smoke", requested_by="m", requester_class="maintainer"), pol, None
    )
    assert d.run_smoke


def test_repo_config_can_only_tighten():
    pol = deep_merge(DEFAULT_POLICY, {"defaults": {"smoke": {"mode": "all"}}})
    cfg, _ = repo_config({"trust": {"smoke": {"stranger": "manual"}}})
    assert not decide(pr(), Trigger(kind="push"), pol, cfg).run_smoke
    # loosening is ignored
    pol2 = deep_merge(DEFAULT_POLICY, {"defaults": {"smoke": {"mode": "manual"}}})
    cfg2, _ = repo_config({"trust": {"smoke": {"stranger": "all"}}})
    assert not decide(pr(), Trigger(kind="push"), pol2, cfg2).run_smoke


def test_per_repo_override():
    pol = deep_merge(DEFAULT_POLICY, {"repos": {"o/r": {"smoke": {"mode": "all"}}}})
    assert decide(pr(), Trigger(kind="push"), pol, None).run_smoke


def test_deep_never_automatic_and_needs_allowed_requester():
    d = decide(pr(permission="admin", labels=["crosscheck:deep"]), Trigger(kind="push"), DEFAULT_POLICY, None)
    assert not d.run_deep
    d = decide(
        pr(),
        Trigger(kind="slash-command", stage="deep", requested_by="s", requester_class="stranger"),
        DEFAULT_POLICY,
        None,
    )
    assert not d.run_deep
    d = decide(
        pr(),
        Trigger(kind="slash-command", stage="deep", requested_by="m", requester_class="maintainer"),
        DEFAULT_POLICY,
        None,
    )
    assert d.run_deep and d.run_smoke


def test_matrix_labels():
    pol = deep_merge(DEFAULT_POLICY, {"defaults": {"smoke": {"mode": "all"}}})
    assert decide(pr(labels=["crosscheck:matrix"]), Trigger(kind="push"), pol, None).matrix == "label_matrix"
    assert decide(pr(), Trigger(kind="push"), pol, None).matrix == "default"


def test_repo_config_normalises_steps():
    cfg, warnings = repo_config(
        {
            "smoke": {
                "steps": [
                    {"do": "Click", "actions": [{"action": "click", "x": 1, "y": 2}], "expect_change": True},
                    {"nope": 1},
                ]
            }
        }
    )
    assert cfg["smoke"]["steps"][0]["actions"][0]["action"] == "click"
    assert len(cfg["smoke"]["steps"]) == 1 and warnings
