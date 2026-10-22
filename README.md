# Jira Dominator

A Jira Cloud tool your editor can call. You download one file, give it your own API token, and point Cursor at it. You do not clone this repo to use it.

Releases: https://github.com/mikewhobs/jira_dominator/releases

## 1. Download the file for your computer

Open the latest release and download one of these:

| Your computer | File |
| --- | --- |
| Mac with Apple silicon (M1, M2, M3, M4, or newer) | `jira-dominator-macos-arm64` |
| Linux, 64-bit Intel or AMD | `jira-dominator-linux-x64` |
| Windows, 64-bit | `jira-dominator-windows-x64.exe` |

Check the Mac chip before you download. Apple menu → About This Mac. If it says Intel, stop. There is no Intel Mac build.

`SHA256SUMS` on the release is optional. Use it if you want to confirm the download was not corrupted.

## 2. Put the file somewhere permanent

Leave it out of Downloads. Replacing the file in this same path is how you update later, and your editor config does not have to change.

### Mac

```bash
mkdir -p "$HOME/.local/bin"
mv "$HOME/Downloads/jira-dominator-macos-arm64" "$HOME/.local/bin/jira-dominator"
xattr -d com.apple.quarantine "$HOME/.local/bin/jira-dominator"
chmod +x "$HOME/.local/bin/jira-dominator"
```

The file is not notarized. macOS may still say it cannot be opened. If that happens: System Settings → Privacy & Security → Open Anyway. Then run the `xattr` command above again if it still refuses.

The path you will paste into Cursor is:

```bash
echo "$HOME/.local/bin/jira-dominator"
```

### Linux

```bash
mkdir -p "$HOME/.local/bin"
mv "$HOME/Downloads/jira-dominator-linux-x64" "$HOME/.local/bin/jira-dominator"
chmod +x "$HOME/.local/bin/jira-dominator"
echo "$HOME/.local/bin/jira-dominator"
```

### Windows

1. Create a folder `C:\Users\YOURNAME\bin`.
2. Move `jira-dominator-windows-x64.exe` into that folder.
3. The path you will paste is `C:\Users\YOURNAME\bin\jira-dominator-windows-x64.exe`.

Windows SmartScreen may warn on the first run because the file is unsigned. Choose "More info", then "Run anyway".

## 3. Create a Jira API token

The binary does not contain a token. Yours stays on your machine, in a file Cursor reads. Do not paste the token into `mcp.json`.

1. Open https://id.atlassian.com/manage-profile/security/api-tokens
2. Create an API token.
3. Copy it immediately. Atlassian shows it once.
4. You need three values:
   - `JIRA_URL`: your site, like `https://yourcompany.atlassian.net`. No trailing slash. Not a browse URL with `/jira/software`.
   - `JIRA_EMAIL`: the email you use to log into that site.
   - `JIRA_API_TOKEN`: the token you just created.

That user must be allowed to see the projects you want the tool to touch.

### Mac and Linux

```bash
mkdir -p "$HOME/.config"
touch "$HOME/.config/jira-dominator.env"
chmod 600 "$HOME/.config/jira-dominator.env"
```

Open that file and put exactly these three lines in it, with your values:

```
JIRA_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=paste-the-token-here
```

The path you will paste into Cursor is:

```bash
echo "$HOME/.config/jira-dominator.env"
```

### Windows

Create the folder `C:\Users\YOURNAME\.config`. Create the file `C:\Users\YOURNAME\.config\jira-dominator.env` in it, with the same three lines. That full path is what you paste into Cursor.

## 4. Tell Cursor to run it

Open Cursor Settings → MCP, or edit `~/.cursor/mcp.json`.

`command` is the binary from step 2. `envFile` is the file from step 3. Both must be absolute paths. Do not use `~`. Do not put the token in this file. Do not point `command` at `python`.

```json
{
  "mcpServers": {
    "jira-dominator": {
      "command": "/ABSOLUTE/PATH/FROM/STEP/2",
      "envFile": "/ABSOLUTE/PATH/FROM/STEP/3"
    }
  }
}
```

If you already pasted a token into `mcp.json`, delete those `env` lines and use `envFile` instead.

Save the file. Cursor should list `jira-dominator` and connect. If it stays red, work through the failures below before you ask for help. Do not commit the env file, and do not put it inside a git checkout.

## 5. Check that it works

In a Cursor chat, ask it to test the Jira connection with the jira-dominator tools. A working server returns your Jira display name. Then try a real read, such as "get the details of PROJ-123".

Do not paste your API token into chat.

## When it fails

| What you see | What to do |
| --- | --- |
| macOS: "cannot be opened" or "damaged" | Run the `xattr` command in step 2. Then Privacy & Security → Open Anyway. |
| Cursor says the command was not found | `command` is not an absolute path, or the file is still in Downloads under a different name. |
| Connection error, 401, or 403 | The email, token, and site URL in the env file do not match. Create a new token. `JIRA_URL` must be the site root. |
| Tools never appear | `envFile` is missing, or it is not an absolute path. Save `mcp.json`, then reload MCP in Cursor Settings. The server only speaks MCP on stdin. Do not run it in a terminal and expect a prompt. |
| Intel Mac | No build for that chip. |

## Updating

Download the new file from the latest release. Replace the file at the same path you put in `command`. Keep the name. Your token stays in the env file.

## Work on the source

You only need this if you are changing the tool.

```bash
git clone https://github.com/mikewhobs/jira_dominator.git
cd jira_dominator
cp .env.example .env
```

Put the same three `JIRA_*` values in `.env`. Python 3.12 is required. Then:

```bash
./run_mcp_server.sh
```

That starts the server from source. Day-to-day use should stay on the release binary.
