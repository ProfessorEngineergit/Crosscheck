"""Trust classes and run decisions.

The policy never weakens isolation. It only decides whether a stage runs automatically and
how much it may use. See docs/11-admin-and-trust-policies.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import STRICTNESS, TRUST_CLASSES


@dataclass
class AuthorFacts:
    """Facts about a PR author, gathered live from the GitHub API at decision time."""

    login: str
    permission: str = "none"  # admin | maintain | write | triage | read | none
    is_bot: bool = False
    teams: list[str] = field(default_factory=list)
    merged_prs: int = 0


@dataclass
class PrFacts:
    repo: str
    number: int
    head_sha: str
    base_sha: str
    author: AuthorFacts
    labels: list[str] = field(default_factory=list)
    # logins of users with write access whose APPROVED review references head_sha
    approvals_on_head: list[str] = field(default_factory=list)
    is_fork: bool = False


@dataclass
class Trigger:
    kind: str  # push | approval | label | slash-command | mcp | nightly | replay
    stage: str | None = None  # requested stage for label/slash/mcp
    requested_by: str | None = None
    requester_class: str | None = None


@dataclass
class Decision:
    trust_class: str
    policy_mode: str
    run_static: bool = False
    run_smoke: bool = False
    run_deep: bool = False
    matrix: str = "default"  # default | label_run | label_matrix
    limits: dict = field(default_factory=dict)
    approved_by: str | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def anything(self) -> bool:
        return self.run_static or self.run_smoke or self.run_deep


WRITE_PERMISSIONS = {"admin", "maintain", "write"}


def trust_class(author: AuthorFacts, policy: dict) -> str:
    classes = policy.get("classes", {})
    login = author.login.lower()
    if login in {u.lower() for u in classes.get("blocked", {}).get("users", [])}:
        return "blocked"
    if author.permission in WRITE_PERMISSIONS:
        return "maintainer"
    bots = {u.lower() for u in classes.get("bot", {}).get("users", [])}
    if author.is_bot or login in bots:
        return "bot"
    trusted = classes.get("trusted", {})
    if login in {u.lower() for u in trusted.get("users", [])}:
        return "trusted"
    if set(trusted.get("teams", [])) & set(author.teams):
        return "trusted"
    if author.merged_prs > 0:
        return "known"
    return "stranger"


def class_mode(stage: str, klass: str, repo: str, policy: dict, repo_cfg: dict | None) -> tuple[str, str]:
    """Effective per-class mode for a stage: auto | approved | manual | off.

    Returns (per_class_mode, configured_policy_mode). The repository config can only make the
    per-class mode stricter.
    """
    stage_cfg = dict(policy["defaults"][stage])
    stage_cfg.update((policy.get("repos", {}).get(repo, {}) or {}).get(stage, {}) or {})
    mode = stage_cfg.get("mode", "manual")
    if klass == "blocked":
        per_class = "auto" if stage == "static" else "off"
    elif mode == "all":
        per_class = "auto"
    elif mode == "classes":
        per_class = "auto" if klass in stage_cfg.get("classes", []) else stage_cfg.get("others", "manual")
    else:
        per_class = mode
    if per_class == "all":
        per_class = "auto"
    # repository tightening
    repo_mode = ((repo_cfg or {}).get("trust", {}).get(stage, {}) or {}).get(klass)
    if repo_mode in STRICTNESS and STRICTNESS[repo_mode] > STRICTNESS[per_class]:
        per_class = repo_mode
    return per_class, mode


def decide(pr: PrFacts, trigger: Trigger, policy: dict, repo_cfg: dict | None) -> Decision:
    klass = trust_class(pr.author, policy)
    limits = dict(policy["limits"].get(klass, {}))
    smoke_mode, configured = class_mode("smoke", klass, pr.repo, policy, repo_cfg)
    static_mode, _ = class_mode("static", klass, pr.repo, policy, repo_cfg)
    deep_mode, _ = class_mode("deep", klass, pr.repo, policy, repo_cfg)
    d = Decision(trust_class=klass, policy_mode=configured, limits=limits)

    maintainer_request = trigger.kind in ("label", "slash-command", "mcp") and trigger.requester_class == "maintainer"

    # static: cheap and executes no PR code
    d.run_static = static_mode != "off"
    if not d.run_static:
        d.reasons.append("static disabled by policy")

    # smoke
    auto_triggers = ("push", "approval", "nightly", "replay")
    if klass == "blocked":
        d.reasons.append(f"author {pr.author.login} is blocked")
    elif smoke_mode == "off":
        d.reasons.append("smoke disabled by policy")
    elif smoke_mode == "auto" and trigger.kind in auto_triggers:
        d.run_smoke = True
        d.reasons.append(f"smoke automatic for class {klass}")
    elif smoke_mode == "approved":
        if pr.approvals_on_head:
            d.run_smoke = True
            d.approved_by = pr.approvals_on_head[0]
            d.reasons.append(f"approved on {pr.head_sha[:12]} by {d.approved_by}")
        else:
            d.reasons.append("waiting for a maintainer approval on this exact commit")
    else:
        d.reasons.append("smoke runs only on request for this class")
    if (
        not d.run_smoke
        and maintainer_request
        and trigger.stage in ("smoke", "deep")
        and smoke_mode != "off"
        and klass != "blocked"
    ):
        d.run_smoke = True
        d.reasons.append(f"requested by maintainer {trigger.requested_by}")

    if "crosscheck:matrix" in pr.labels and d.run_smoke:
        d.matrix = "label_matrix"
    elif ("crosscheck:run" in pr.labels or maintainer_request) and d.run_smoke:
        d.matrix = "label_run"

    # deep: never automatic, always an explicit request by an allowed requester
    deep_requested = trigger.stage == "deep" or (trigger.kind == "label" and "crosscheck:deep" in pr.labels)
    if deep_requested:
        allowed = policy["deep"].get("allowed_requesters", ["maintainer"])
        if deep_mode == "off" or klass == "blocked":
            d.reasons.append("deep disabled by policy")
        elif trigger.requester_class in allowed:
            d.run_deep = True
            d.run_smoke = d.run_smoke or smoke_mode != "off"
            d.reasons.append(f"deep requested by {trigger.requested_by}")
        else:
            d.reasons.append("deep review needs a request from an allowed requester")
    return d


def explain(decision: Decision) -> str:
    stages = [
        s
        for s, on in (("static", decision.run_static), ("smoke", decision.run_smoke), ("deep", decision.run_deep))
        if on
    ]
    lines = [
        f"trust class: {decision.trust_class}",
        f"policy mode: {decision.policy_mode}",
        f"stages: {', '.join(stages) or 'none'}",
        f"matrix: {decision.matrix}",
    ]
    lines += [f"- {r}" for r in decision.reasons]
    return "\n".join(lines)


def is_valid_class(name: str) -> bool:
    return name in TRUST_CLASSES
