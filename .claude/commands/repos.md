Read ~/wingmen/orchestrator/REPOS.json and for each repo:
1. Check if the local clone exists at its local_path
2. If it exists, run `git status` and `git log --oneline -3` in that directory
3. Report: repo name, status, priority, last 3 commits, any uncommitted changes

Give a concise table overview of all repos.