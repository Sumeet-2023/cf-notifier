# Codeforces rating notifier

Pings your group on **Discord**, **Telegram** and/or **ntfy** (phone push) as soon as Codeforces publishes rating changes for a contest. It runs free on GitHub Actions: no server, no paid services, no dependencies.

```
📈 Codeforces ratings updated!

🏁 Codeforces Round 1043 (Div. 2)
28,114 rated participants · https://codeforces.com/contest/2141/standings
• alice: 1560 → 1720 (+160), rank 312
• bob: 1380 → 1320 (−60), rank 9001
```

The per-person lines only appear if you list handles in `CF_HANDLES`. When Div. 1 and Div. 2 results come out together, they're combined into one message.

## How it works

Every 10 minutes a GitHub Actions job asks the Codeforces API whether any contest that finished in the last 7 days has published rating changes. When one has, the job sends a message and records the contest ID in `state.json` (committed back to the repo), so each contest is announced exactly once.

- **First run** only records contests that are already rated. It sends nothing, so your group doesn't get a flood of old results.
- **Codeforces down** (it happens): the run skips quietly and tries again 10 minutes later. You won't get failure emails for it.
- **A channel fails** (e.g. a deleted Discord webhook): the other channels still get the message, and the run is marked failed so GitHub emails you.

## Setup (about 10 minutes)

### 1. Create the repo

Create a **public** GitHub repository and add these files, keeping the folder structure:

```
cf_rating_notifier.py
.github/workflows/cf-rating.yml
```

On github.com: **Add file → Upload files** works for the script. For the workflow, use **Add file → Create new file**, type `.github/workflows/cf-rating.yml` as the name, and paste the contents.

Why public: GitHub Actions is free and unlimited for public repos. Your tokens stay hidden in Secrets. Private repos get 2,000 free minutes a month, and checking every 10 minutes uses about 4,300, so for a private repo change the cron line to `"4-59/30 * * * *"` (every 30 minutes).

### 2. Pick where the pings go (one or more)

Add each value under **Settings → Secrets and variables → Actions → New repository secret**.

**Discord**
1. In your server: **Server Settings → Integrations → Webhooks → New Webhook**, choose the channel, **Copy Webhook URL**.
2. Secret `DISCORD_WEBHOOK_URL` = that URL.
3. Optional: to @everyone-ping the server, open the **Variables** tab and add `DISCORD_PING_EVERYONE` = `true`.

**Telegram**
1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token. Secret `TELEGRAM_BOT_TOKEN` = token.
2. Add the bot to your group (or as an admin of your channel).
3. Secret `TELEGRAM_CHAT_ID`:
   - Public channel: `@yourchannelname`
   - Group: send `/start@YourBotName` in the group, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the number after `"chat":{"id":` (it looks like `-1001234567890`).

**ntfy (phone push notifications, no account needed)**
1. Generate a random topic name. Anyone who knows the name can read the pings, so don't make one up yourself:
   ```
   python3 cf_rating_notifier.py --new-topic
   ```
   It prints something like `cf-7k3m-q9xp-4hwr-ne2d`: 16 random characters from Python's `secrets` module (the one meant for passwords), which is far too many combinations to guess. It skips look-alike characters like `0`/`o` and `1`/`l`, so it's easy to type on a phone. On Windows, use `python` instead of `python3`.
2. Secret `NTFY_TOPIC` = that name.
3. Share the name only with your group. Everyone installs the ntfy app ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/app/ntfy/id1625396347)), taps **+**, and subscribes to the topic. On a computer, open `https://ntfy.sh/<topic>`.

Anyone who knows the name can also *send* messages to the topic, so a leaked name means strangers could read your pings or post fake ones. If that happens, run `--new-topic` again, update the secret, and have everyone subscribe to the new name. The script refuses topic names shorter than 12 characters.

**Optional: show your group's rating changes**
Under **Variables**, add `CF_HANDLES` = `your_handle, friend1, friend2` (comma-separated, case doesn't matter).

### 3. Test it

1. Open the **Actions** tab → **Codeforces rating notifier** → **Run workflow**, tick **Send a test message**, and run it. A "✅ set up" message should arrive in every channel you configured.
2. Run it once more **without** the tick. This is the first real run: it records current results and commits `state.json`.

From then on it runs by itself.

## Good to know

- **Timing:** GitHub often starts scheduled jobs a few minutes late, so expect the ping 5–20 minutes after ratings go live.
- **Inactivity:** GitHub pauses scheduled workflows in public repos after 60 days with no repo activity. The bot commits `state.json` after every rated round, which counts as activity, so this normally never happens. If it does, GitHub emails you and you click **Enable workflow** in the Actions tab.
- **Public logs:** run logs of a public repo are visible to anyone. They only name the contests. Your group's handles and rating changes go to your chat, not the log, and secrets are masked (the script also removes them from its own error messages).
- **Run it elsewhere:** the script is plain Python 3 with no dependencies. Any machine with cron works too: `DISCORD_WEBHOOK_URL=... python3 cf_rating_notifier.py`

## Security

- **Who can run it:** the workflow runs only on its schedule or when someone with write access clicks **Run workflow**. Pull requests and forks can't trigger it or read your secrets.
- **Least privilege:** the repo token can only write repo contents and exists only in the final `git push` step. The Python step, which handles data from Codeforces, never sees it. Even that token can't edit workflow files.
- **Pinned dependency:** the only third-party code is `actions/checkout`, pinned to an exact commit. The script uses only Python's standard library, so nothing is downloaded from PyPI.
- **Checked settings:** webhook and server URLs must be `https`, the Discord URL must point to `discord.com`, and malformed tokens or topics are refused before anything is sent.
- **Your GitHub account is the key:** anyone who gets into it can change the code or read the secrets through a workflow. Turn on two-factor authentication.
- **If you add more workflows later,** don't use the `pull_request_target` trigger unless you know how it works. It runs with your secrets on code from strangers' pull requests.
- **If a secret leaks,** replace it at the source: create a new Discord webhook and delete the old one, use `/revoke` with @BotFather for Telegram, or run `--new-topic` for ntfy. Then update the repo secret.
- **Updating `actions/checkout`:** to move to a newer version, replace the commit hash with the one GitHub lists for that release tag, and keep the `# vX.Y.Z` comment in sync.
