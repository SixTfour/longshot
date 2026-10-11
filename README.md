# Longshot

A Discord bot that runs a Sleeper fantasy league's weekly parlay.

Each week the lowest-scoring team owes the stake, and every member adds one NFL bet to a league parlay. Longshot finds the lowest scorer, collects the picks, posts the slip, tracks every leg live, and keeps the stats and the ledger.

It never places a bet or moves money. The commissioner still places the parlay and collects the stake; Longshot records both.

## What a week looks like

| When (US Eastern) | What happens |
|---|---|
| Tuesday 10:00 | Longshot posts last week's lowest scorer from Sleeper, with a **Mark paid** button, and opens picks. |
| Until the deadline | Members find a bet in the sportsbook app, then lock it in with `/pick`. |
| 24, 7 and 2 hours before the deadline | Members without a pick are pinged in the channel and by DM. |
| Deadline (Thursday 19:00 by default) | Picks close and the slip is posted. Anyone without a pick is named; no bet is chosen for them. |
| After that | The commissioner places the parlay and records the real odds with `/placed`. |
| During games | Each leg is posted as it is settled. The first miss is called out as the parlay killer. |
| After each slate | A summary of that slate's legs, whether the parlay is alive, and what is next. |
| After the last leg | The result, and the payout split if it hit. |

Only games that kick off after the deadline can be picked, so a Sunday deadline leaves out Thursday's game. The cycle runs for the 18 weeks of the regular season.

### Holiday weeks

- **Slates come from the real schedule.** Thanksgiving's three games, Black Friday, Christmas Day games, Saturday games and London morning games each get their own slate and their own update.
- **Early weeks move the deadline.** If the deadline is set for Thursday evening or earlier and a week's first game kicks off before it (Thanksgiving week, a Wednesday opener), the deadline for that week moves to one hour before that first kickoff. The announcement says so.
- **A weekend deadline never moves.** A league that only picks Sunday games has chosen to leave out everything earlier.

## Commands

| Command | Who | What it does |
|---|---|---|
| `/pick who bet line` | Members | Lock in a leg. `who` is a player or a team; `line` lists the main line and every alternate. |
| `/mypick` | Members | Show your pick for this week. |
| `/slip [week]` | Members | The parlay and how each leg is doing. |
| `/stats [member]`, `/leaderboard` | Members | Hit rate, streaks, parlays killed, weeks as lowest scorer, deadlines missed. |
| `/ledger` | Members | Who owes, what has been collected, what has been won. |
| `/members` | Members | Which Sleeper team belongs to which Discord member. |
| `/setup league_id channel` | Commissioner | Connect the Sleeper league and choose the channel. |
| `/link member team`, `/unlink member` | Commissioner | Tie a Discord member to a Sleeper team. |
| `/config [setting] [value]` | Commissioner | Show or change the deadline, sportsbook, announcement time, reminders, stake and line cache. |
| `/pickfor member who bet line` | Commissioner | Enter or replace a member's pick, including after the deadline. |
| `/placed odds` | Commissioner | Record that the parlay was placed, with the sportsbook's odds. |
| `/paid member [week]` | Commissioner | Mark a stake as collected. |
| `/grade member result [week]` | Commissioner | Settle a leg by hand, or correct one. |
| `/admin run step [week]` | Commissioner | Run one step of the cycle now. |
| `/admin loser member [week]` | Commissioner | Set who pays, for a tie or a correction. |
| `/admin status` | Commissioner | How the bot is set up, this week's times, and Odds API credits left. |

The commissioner is whoever ran `/setup`. Anyone with the server's Manage Server permission can also use the commissioner commands.

### Bets supported

- **Team:** moneyline, spread, game total, with alternate spreads and totals.
- **Player:** passing yards, passing TDs, completions, attempts, interceptions thrown, rushing yards, rushing attempts, receptions, receiving yards, rush + receiving yards, pass + rush yards, kicking points, field goals made, anytime TD. Every one except anytime TD includes alternate lines.

Only bets the sportsbook feed lists can be picked. Two members cannot hold the same leg, or direct opposites.

### How legs are graded

- A leg that lands exactly on its line is a push and drops out of the parlay.
- A prop on a player who did not play is void and drops out.
- Counting stats (receptions, TDs, attempts, field goals) and game totals are settled the moment they pass the line. Yardage waits for the final whistle, because yardage can go down.
- Anytime TD counts rushing, receiving, return and defensive touchdowns. A passing TD does not count for the passer.
- If the bot cannot decide a leg it marks it ❓ and the commissioner settles it with `/grade`.

## Setting up

You need three things. The first two are secrets and go in a file called `.env` that never leaves your machine.

1. **A Discord bot token.** In the [Discord Developer Portal](https://discord.com/developers/applications) create an application, open **Bot**, and press **Reset Token**. No privileged intents are needed.
2. **An Odds API key.** Sign up at [the-odds-api.com](https://the-odds-api.com). The free plan gives 500 credits a month.
3. **Your Sleeper league ID.** It is the long number in your league's web address. It is not a secret; you give it to `/setup`.

Invite the bot to your server with this link, replacing `YOUR_APP_ID` with the application's ID:

```
https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=84992
```

That grants View Channels, Send Messages, Embed Links and Read Message History.

Then create `.env` from the template and fill in the two secrets:

```
cp .env.example .env
```

Run only one copy of the bot at a time. Two copies with the same token would both post.

### Running on a Raspberry Pi

Longshot is light enough for a Pi 2B. It needs Raspberry Pi OS Bookworm or later (Python 3.11+); the 32-bit version is fine.

```bash
sudo apt update && sudo apt install -y git gh python3-venv
gh auth login                        # the repository is private
gh repo clone SixTfour/longshot && cd longshot

python3 -m venv .venv
.venv/bin/pip install --prefer-binary -e .

cp .env.example .env && nano .env    # paste the two secrets
chmod 600 .env

.venv/bin/python -m longshot         # try it; Ctrl+C to stop
```

`--prefer-binary` makes pip use the prebuilt packages from piwheels, so nothing is compiled on the Pi.

To keep it running and start it at boot:

```bash
sudo cp deploy/longshot.service /etc/systemd/system/
sudo nano /etc/systemd/system/longshot.service   # set User and the two paths if yours differ
sudo systemctl enable systemd-time-wait-sync     # start only once the clock is right
sudo systemctl daemon-reload
sudo systemctl enable --now longshot
journalctl -u longshot -f                        # watch the log
```

To update later: `git pull && sudo systemctl restart longshot`.

The database is kept at `~/.longshot/longshot.db`. Copy that one file to back up or move the bot.

### Running on Windows

```powershell
python -m venv $env:LOCALAPPDATA\Longshot\venv
& $env:LOCALAPPDATA\Longshot\venv\Scripts\pip install -e ".[dev]"
& $env:LOCALAPPDATA\Longshot\venv\Scripts\python -m longshot
```

The environment and the database live under `%LOCALAPPDATA%\Longshot`, outside this folder, so that OneDrive does not sync them. The bot is online for as long as that window stays open.

### First run in Discord

1. `/setup league_id:<your id> channel:#your-channel`
2. `/link member:@someone team:<their team>` for each member, then `/members` to check.
3. `/config` to review the settings.
4. `/admin status` shows this week's announcement and deadline times.

If the bot is first started after a week's deadline, it announces the lowest scorer and starts collecting picks the following week.

## Settings

Change these with `/config`. They take effect without a restart.

| Setting | Default | Notes |
|---|---|---|
| Pick deadline | `thu 19:00` | Day and time, US Eastern. Applies to the current week if it has not closed. |
| Sportsbook | `draftkings` | Any bookmaker key from The Odds API, such as `fanduel` or `betmgm`. |
| Loser announcement | `tue 10:00` | When the lowest scorer is posted and picks open. |
| Reminders | `24, 7, 2` | Hours before the deadline. |
| Stake | `10` | What the lowest scorer owes. |
| Line cache | `12` | Hours a fetched list of lines is reused. Lower for fresher prices, higher to save credits. |

## Odds API credits

| Lookup | Cost |
|---|---|
| Typing a player or team name | Free (names come from ESPN) |
| Moneyline, spread and total for every game | 3 credits, reused for the cache period |
| Alternate spreads or totals for one game | 1 credit |
| One player prop, main and alternate lines | 2 credits |

When fewer than 50 credits are left the bot stops fetching new lists, tells the commissioner, and keeps serving the lists it already has. `/admin status` shows the balance.

## Secrets

- The Discord token and Odds API key live only in `.env`, which is git-ignored.
- `.githooks/pre-commit` refuses a commit that contains a `.env` file, a value from `.env`, or a string shaped like a token. Turn it on once per clone with `git config core.hooksPath .githooks`.
- The Odds API key travels in request URLs, so every URL is scrubbed before it is logged.
- If a secret is ever exposed, reset it where it was issued. Deleting it from git history is not enough.

## Development

```
pytest
```

The tests grade every market against a real ESPN box score, check slates and deadlines against the real 2026 schedule (Thanksgiving and Christmas weeks included), and play out whole weeks against stand-ins for Discord, Sleeper, ESPN and the odds feed.

| Folder | Contents |
|---|---|
| `longshot/domain/` | Pure logic: grading, odds maths, lines, schedule, slates, stats. |
| `longshot/clients/` | Sleeper, The Odds API and ESPN. |
| `longshot/cogs/` | Slash commands. |
| `longshot/weekly.py`, `tracking.py`, `scheduler.py` | The weekly steps, live tracking, and the once-a-minute clock. |

## Data sources

- **Sleeper** for fantasy scores. Official, free, no key.
- **The Odds API** for lines and prices. Needs a key.
- **ESPN** for the schedule, rosters, live scores and box scores. Free, but unofficial and undocumented, so it can change without notice. `/grade` is the fallback if grading ever breaks.
