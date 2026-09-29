---
description: Request a Crosscheck deep security review (uses model budget)
argument-hint: "<owner/repo> <pr-number>"
---

The user wants a deep security review for $ARGUMENTS. This costs model budget. Confirm with the
user first, then call `crosscheck_request_run` with stage=deep and confirm=true. Report the run ID.
When the run is done, summarise findings by severity and whether two reviewers agreed
(`agreement`: confirmed, single, disputed).
