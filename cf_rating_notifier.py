#!/usr/bin/env python3
"""
Codeforces rating-update notifier.

Checks whether Codeforces has published rating changes for any recently
finished contest. When it has, it posts one message to every channel you
configured (Discord, Telegram, ntfy). Each contest is announced only once.

Configuration (environment variables; set whichever channels you use):
  DISCORD_WEBHOOK_URL    Discord channel webhook URL
  DISCORD_PING_EVERYONE  "true" to prefix the Discord message with @everyone
  TELEGRAM_BOT_TOKEN     Telegram bot token from @BotFather
  TELEGRAM_CHAT_ID       Telegram group/channel id (e.g. -1001234567890 or @mychannel)
  NTFY_TOPIC             ntfy.sh topic name (people subscribe in the ntfy app)
  NTFY_SERVER            optional, defaults to https://ntfy.sh
  CF_HANDLES             optional, comma-separated handles to show deltas for
  ONLY_IF_PARTICIPATED   "true" to only notify for contests where a CF_HANDLES handle was rated
  STATE_FILE             optional, defaults to state.json
  LOOKBACK_DAYS          optional, how far back to look for finished contests (default 7)

Usage:
  python3 cf_rating_notifier.py              check and notify
  python3 cf_rating_notifier.py --test       send a test message to every channel
  python3 cf_rating_notifier.py --new-topic  print a random, hard-to-guess ntfy topic name
"""

import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://codeforces.com/api/"
USER_AGENT = "cf-rating-notifier/1.1"
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS") or 7)
MAX_REMEMBERED = 300  # contest ids kept in state.json


class CodeforcesDown(Exception):
    """Codeforces could not be reached or kept refusing the call."""


class ConfigError(Exception):
    """A channel setting is malformed or unsafe."""


SECRET_VARS = ("DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "NTFY_TOPIC")


def redact(text):
    """Remove secret values from text before it is printed.

    GitHub already masks secrets in logs, but only exact matches. This also
    covers local runs and the token part of a Discord webhook URL on its own.
    """
    text = str(text)
    for key in SECRET_VARS:
        value = os.environ.get(key, "").strip()
        pieces = [value]
        if key == "DISCORD_WEBHOOK_URL":
            pieces.append(value.rstrip("/").rsplit("/", 1)[-1])  # the webhook token
        for p in pieces:
            if len(p) >= 4:
                text = text.replace(p, "***")
    return text


def truthy(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def warn(msg):
    # Shows up as a yellow annotation on GitHub Actions, plain text elsewhere.
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "WARNING: "
    print(prefix + msg)


# ---------------------------------------------------------------- HTTP helpers

def http_get_json(url):
    """GET a URL and return (status_code, parsed_json_or_None)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        # Safe: only ever called with the fixed https Codeforces API base.
        with urllib.request.urlopen(req, timeout=60) as r:  # nosec B310
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        # Codeforces answers FAILED calls with HTTP 400 and a JSON body.
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, None


def http_post(url, body, headers):
    """POST bytes; raise on a non-2xx answer."""
    h = {"User-Agent": USER_AGENT}
    h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    # Safe: callers pass URLs checked by the validate_* functions (https only).
    with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310
        if not 200 <= r.status < 300:
            raise RuntimeError(f"HTTP {r.status}")


def cf(method, **params):
    """Call the Codeforces API and return the parsed JSON dict.

    Codeforces allows about one call every 2 seconds, so every call sleeps
    afterwards. Network errors, non-JSON answers (e.g. maintenance pages) and
    "Call limit exceeded" are retried; after that CodeforcesDown is raised.
    """
    url = API + method + ("?" + urllib.parse.urlencode(params) if params else "")
    last_err = None
    for attempt in range(4):
        try:
            code, data = http_get_json(url)
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(5 * (attempt + 1))
            continue
        time.sleep(2.1)
        if not isinstance(data, dict):
            last_err = f"HTTP {code} with a non-JSON body"
            time.sleep(5 * (attempt + 1))
            continue
        comment = str(data.get("comment", ""))
        if data.get("status") == "FAILED" and "limit" in comment.lower():
            last_err = comment
            time.sleep(5 * (attempt + 1))
            continue
        return data
    raise CodeforcesDown(f"{method}: {last_err}")


# ---------------------------------------------------------------- state

def load_state():
    """Return (notified_ids: list[int], first_run: bool)."""
    if not os.path.exists(STATE_FILE):
        return [], True
    with open(STATE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return [int(x) for x in data.get("notified", [])], False


def save_state(notified):
    notified = notified[-MAX_REMEMBERED:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"notified": notified}, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------- message

def signed(n):
    return f"+{n}" if n > 0 else ("±0" if n == 0 else f"−{abs(n)}")


def build_message(updates, handles):
    """updates: list of (contest_dict, rating_changes_list)."""
    lines = ["📈 Codeforces ratings updated!", ""]
    watch = {h.lower() for h in handles}
    for contest, changes in updates:
        cid = contest["id"]
        lines.append(f"🏁 {contest['name']}")
        people = f"{len(changes):,} rated participant" + ("" if len(changes) == 1 else "s")
        lines.append(f"{people} · https://codeforces.com/contest/{cid}/standings")
        if watch:
            mine = [c for c in changes if c["handle"].lower() in watch]
            mine.sort(key=lambda c: c["newRating"] - c["oldRating"], reverse=True)
            if mine:
                for c in mine:
                    d = c["newRating"] - c["oldRating"]
                    lines.append(
                        f"• {c['handle']}: {c['oldRating']} → {c['newRating']} ({signed(d)}), rank {c['rank']}"
                    )
            else:
                lines.append("• Nobody from your list took part.")
        lines.append("")
    return "\n".join(lines).strip()


def split_text(text, limit):
    """Split text on line breaks into pieces of at most `limit` characters."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single line that is too long on its own
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = line if not cur else cur + "\n" + line
        if len(candidate) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = candidate
    if cur.strip():
        parts.append(cur)
    return parts


# ---------------------------------------------------------------- config checks
# Settings are checked before anything is sent, so a typo'd or tampered value
# can't send your messages (or the ntfy topic) to some other server over plain http.

DISCORD_HOSTS = ("discord.com", "discordapp.com")
TELEGRAM_TOKEN_RE = re.compile(r"\d+:[A-Za-z0-9_-]+")
NTFY_TOPIC_RE = re.compile(r"[-_A-Za-z0-9]{1,64}")
MIN_TOPIC_LEN = 12


def validate_discord_url(url):
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    ok_host = any(host == h or host.endswith("." + h) for h in DISCORD_HOSTS)
    if p.scheme != "https" or not ok_host or not p.path.startswith("/api/webhooks/"):
        raise ConfigError("DISCORD_WEBHOOK_URL must be a https://discord.com/api/webhooks/... URL")
    return url


def validate_telegram_token(token):
    if not TELEGRAM_TOKEN_RE.fullmatch(token):
        raise ConfigError("TELEGRAM_BOT_TOKEN doesn't look like a BotFather token (123456:ABC...)")
    return token


def validate_ntfy(server, topic):
    if urllib.parse.urlsplit(server).scheme != "https":
        raise ConfigError("NTFY_SERVER must start with https://")
    if not NTFY_TOPIC_RE.fullmatch(topic):
        raise ConfigError("NTFY_TOPIC may only contain letters, digits, - and _ (max 64)")
    if len(topic) < MIN_TOPIC_LEN:
        raise ConfigError(
            f"NTFY_TOPIC is too short to be secret (under {MIN_TOPIC_LEN} characters); "
            "run: python3 cf_rating_notifier.py --new-topic"
        )
    return server, topic


# ---------------------------------------------------------------- channels

def send_discord(text):
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return None
    validate_discord_url(url)
    ping = truthy("DISCORD_PING_EVERYONE")
    for i, part in enumerate(split_text(text, 1900)):
        payload = {
            "content": ("@everyone " if ping and i == 0 else "") + part,
            "allowed_mentions": {"parse": ["everyone"] if ping and i == 0 else []},
        }
        if i:
            time.sleep(1)  # stay well inside Discord's webhook rate limit
        http_post(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    return "Discord"


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat):
        return None
    validate_telegram_token(token)
    for i, part in enumerate(split_text(text, 4000)):
        if i:
            time.sleep(1)
        payload = {"chat_id": chat, "text": part, "disable_web_page_preview": True}
        http_post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )
    return "Telegram"


def send_ntfy(text):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return None
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").strip().rstrip("/")
    validate_ntfy(server, topic)
    for i, part in enumerate(split_text(text, 3000)):  # ntfy caps a message at 4096 bytes
        if i:
            time.sleep(1)
        http_post(
            f"{server}/{urllib.parse.quote(topic)}",
            part.encode("utf-8"),
            {"Title": "Codeforces ratings updated", "Tags": "chart_with_upwards_trend"},
        )
    return "ntfy"


CHANNELS = [send_discord, send_telegram, send_ntfy]


def broadcast(text):
    """Send to every configured channel. Returns (sent, failed, configured)."""
    sent, failed = [], []
    for fn in CHANNELS:
        try:
            name = fn(text)
            if name:
                sent.append(name)
        except Exception as e:  # keep going so one broken channel doesn't block the others
            detail = str(e) if isinstance(e, ConfigError) else f"{type(e).__name__}: {e}"
            failed.append(f"{fn.__name__.replace('send_', '')}: {redact(detail)}")
    return sent, failed, len(sent) + len(failed)


# ---------------------------------------------------------------- ntfy topic

# No look-alike characters (0/o, 1/l/i), so the name is easy to type on a phone.
TOPIC_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def new_topic():
    """Random topic like cf-7k3m-q9xp-4hwr-ne2d: 16 characters from `secrets`
    (a cryptographically secure generator), about 79 bits, so it can't be guessed."""
    groups = ["".join(secrets.choice(TOPIC_ALPHABET) for _ in range(4)) for _ in range(4)]
    return "cf-" + "-".join(groups)


def print_new_topic():
    topic = new_topic()
    print(topic)
    print()
    print("1. Save it as the NTFY_TOPIC secret in your GitHub repo.")
    print("2. Share it only with your group. Phone: ntfy app → + → enter the topic → Subscribe.")
    print(f"   Computer: open https://ntfy.sh/{topic}")
    print("Run this again for a different name.")


# ---------------------------------------------------------------- main

def main():
    if "--new-topic" in sys.argv:
        print_new_topic()
        return 0

    handles = [h.strip() for h in os.environ.get("CF_HANDLES", "").split(",") if h.strip()]

    if "--test" in sys.argv:
        sent, failed, configured = broadcast(
            "✅ Codeforces rating notifier is set up. You'll get a message here when ratings update."
        )
        if configured == 0:
            print("No channel configured. Set DISCORD_WEBHOOK_URL, TELEGRAM_* or NTFY_TOPIC.")
            return 1
        print("Test sent to:", ", ".join(sent) or "none")
        for f in failed:
            print("FAILED:", f)
        return 1 if failed else 0

    only_mine = truthy("ONLY_IF_PARTICIPATED")
    if only_mine and not handles:
        print("ONLY_IF_PARTICIPATED is on, but CF_HANDLES is empty. Add your handle to CF_HANDLES.")
        return 1

    notified, first_run = load_state()
    seen = set(notified)

    # A Codeforces outage is normal and temporary: skip this run quietly
    # (exit 0) so GitHub doesn't send a failure email every 10 minutes.
    try:
        data = cf("contest.list", gym="false")
    except CodeforcesDown as e:
        warn(f"Codeforces unreachable, will retry next run ({e})")
        return 0
    if data.get("status") != "OK":
        warn(f"contest.list failed, will retry next run ({data.get('comment')})")
        return 0

    since = time.time() - LOOKBACK_DAYS * 86400
    candidates = [
        c for c in data["result"]
        if c.get("phase") == "FINISHED"
        and c["id"] not in seen
        and c.get("startTimeSeconds", 0) + c.get("durationSeconds", 0) >= since
    ]
    candidates.sort(key=lambda c: c.get("startTimeSeconds", 0))

    updates = []
    for c in candidates:
        try:
            rc = cf("contest.ratingChanges", contestId=c["id"])
        except CodeforcesDown as e:
            warn(f"Skipping {c['name']} until next run ({e})")
            continue
        # Unrated contests answer FAILED; rated ones answer an empty list until ratings are out.
        if rc.get("status") == "OK" and rc.get("result"):
            updates.append((c, rc["result"]))

    if first_run:
        # Don't announce contests that were already updated before the notifier existed.
        notified += [c["id"] for c, _ in updates]
        save_state(notified)
        print(f"First run: remembered {len(updates)} already-updated contest(s); nothing sent.")
        return 0

    skipped = []
    if only_mine:
        # Keep only contests where someone from CF_HANDLES got a rating change.
        # The rest are remembered as done, so they're never checked again.
        watch = {h.lower() for h in handles}
        skipped = [c for c, ch in updates if not any(x["handle"].lower() in watch for x in ch)]
        updates = [(c, ch) for c, ch in updates if any(x["handle"].lower() in watch for x in ch)]
        if skipped:
            notified += [c["id"] for c in skipped]
            save_state(notified)
            print("Ratings out, but none of your handles took part in:", "; ".join(c["name"] for c in skipped))

    if not updates:
        if not (only_mine and skipped):
            print(f"No new rating updates ({len(candidates)} finished contest(s) still pending or unrated).")
        return 0

    text = build_message(updates, handles)
    # Logs of a public repo are public: name the contests, but keep your
    # group's handles and rating changes out of them.
    print("Ratings out for:", "; ".join(c["name"] for c, _ in updates))
    sent, failed, configured = broadcast(text)

    if configured == 0:
        print("No channel configured. Set DISCORD_WEBHOOK_URL, TELEGRAM_* or NTFY_TOPIC.")
        return 1
    if not sent:
        # Nothing went out, so don't mark as notified; the next run retries.
        for f in failed:
            print("FAILED:", f)
        return 1

    notified += [c["id"] for c, _ in updates]
    save_state(notified)
    print("Sent to:", ", ".join(sent))
    for f in failed:
        print("FAILED:", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
