Download the file that matches your computer. You do not clone the repo to use this.

| Your computer | File |
| --- | --- |
| Mac, Apple silicon | `jira-dominator-macos-arm64` |
| Linux, 64-bit | `jira-dominator-linux-x64` |
| Windows, 64-bit | `jira-dominator-windows-x64.exe` |

There is no Intel Mac build.

The file does not contain a Jira token. Put the token in a file outside the repo, mode `600`, and point Cursor at it with `envFile`. Do not paste the token into `mcp.json`. Full steps: [README](https://github.com/mikewhobs/jira_dominator#3-create-a-jira-api-token).

Mac, after the download:

```bash
mkdir -p "$HOME/.local/bin"
mv "$HOME/Downloads/jira-dominator-macos-arm64" "$HOME/.local/bin/jira-dominator"
xattr -d com.apple.quarantine "$HOME/.local/bin/jira-dominator"
chmod +x "$HOME/.local/bin/jira-dominator"
mkdir -p "$HOME/.config"
touch "$HOME/.config/jira-dominator.env"
chmod 600 "$HOME/.config/jira-dominator.env"
```

Write `JIRA_URL`, `JIRA_EMAIL`, and `JIRA_API_TOKEN` into that env file. Then:

```json
{
  "mcpServers": {
    "jira-dominator": {
      "command": "/ABSOLUTE/PATH/TO/jira-dominator",
      "envFile": "/ABSOLUTE/PATH/TO/jira-dominator.env"
    }
  }
}
```

Create the token at https://id.atlassian.com/manage-profile/security/api-tokens

The Mac and Windows files are unsigned. macOS may call the download damaged until you run `xattr`. Windows SmartScreen may warn once.
