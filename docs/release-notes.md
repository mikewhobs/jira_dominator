Download the file that matches your computer. You do not clone the repo to use this.

| Your computer | File |
| --- | --- |
| Mac, Apple silicon | `jira-dominator-macos-arm64` |
| Linux, 64-bit | `jira-dominator-linux-x64` |
| Windows, 64-bit | `jira-dominator-windows-x64.exe` |

There is no Intel Mac build.

The file does not contain a Jira token. Put yours in Cursor's MCP config. Full steps, including the macOS quarantine command and the JSON to paste: [README](https://github.com/mikewhobs/jira_dominator#jira-dominator).

Mac, after the download:

```bash
mkdir -p "$HOME/.local/bin"
mv "$HOME/Downloads/jira-dominator-macos-arm64" "$HOME/.local/bin/jira-dominator"
xattr -d com.apple.quarantine "$HOME/.local/bin/jira-dominator"
chmod +x "$HOME/.local/bin/jira-dominator"
```

```json
{
  "mcpServers": {
    "jira-dominator": {
      "command": "/ABSOLUTE/PATH/TO/jira-dominator",
      "env": {
        "JIRA_URL": "https://yourcompany.atlassian.net",
        "JIRA_EMAIL": "you@example.com",
        "JIRA_API_TOKEN": "paste-the-token-here"
      }
    }
  }
}
```

Create the token at https://id.atlassian.com/manage-profile/security/api-tokens

The Mac and Windows files are unsigned. macOS may call the download damaged until you run `xattr`. Windows SmartScreen may warn once.
