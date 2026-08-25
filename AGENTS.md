# Repository workflow

## GitHub synchronization

- The canonical remote repository is `https://github.com/kingKAIWU/DocAlign.git`.
- After completing and verifying user-requested local project changes, commit only the
  task-related files and push the current `main` branch to `origin`, unless the user explicitly
  asks not to push or the work is not ready to publish.
- Inspect the working tree and staged diff before every commit. Preserve unrelated user changes.
- Never commit `.env`, credentials, API keys, local databases, runtime data, user-provided private
  documents, or generated repair/output artifacts.
- Never force-push. If authentication, network access, CI, or remote divergence prevents a safe
  push, stop and report the exact blocker to the user.
