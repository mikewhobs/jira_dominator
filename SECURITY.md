# Security

## Reporting a vulnerability

Do not open a public GitHub issue for a security problem.

Use GitHub private vulnerability reporting on this repository: Security → Report a vulnerability.

If that is unavailable, email the address on the maintainer's GitHub profile with "jira-dominator security" in the subject. Include the version or commit, what you can reach, and whether a token or Jira data was exposed.

## What this project stores

Release binaries do not contain Jira credentials. Tokens belong in a file outside the repo, referenced from Cursor with `envFile`. Do not send tokens, `.env` files, or `mcp.json` contents that include secrets when you report a problem.
