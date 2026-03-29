Deploy the specified repo to Vercel. Steps:
1. Read REPOS.json to find the repo config
2. cd to the repo's local_path
3. Run `git status` to check for uncommitted changes
4. If clean, run `npx vercel --prod` to deploy
5. Update STATUS.md with the deployment result
6. Report back the deploy URL

Repo to deploy: $ARGUMENTS