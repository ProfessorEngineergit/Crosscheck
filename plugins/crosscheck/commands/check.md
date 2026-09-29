---
description: Request a Crosscheck smoke run for a pull request
argument-hint: "<owner/repo> <pr-number> [platforms] [presets]"
---

Request a Crosscheck smoke run for $ARGUMENTS with `crosscheck_request_run` (stage=smoke). Pass
platforms and presets only if given. Report the decision and run ID, then tell the user the result
will appear in `/crosscheck:status` and as a GitHub check. Do not request `stage=deep` from this
command.
