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
| Deadline (Saturday 9:00 PM by default) | Picks close and the slip is posted. Anyone without a pick is named; no bet is chosen for them. |
| After that | The commissioner places the parlay and records the real odds with `/placed`. |
| During games | Each leg is posted as it is settled. The first miss kills the parlay and is called out. |
| After each slate | While the parlay is alive: a summary of that slate's legs and what is next. |
| After the last leg | If every leg hit: the result, every leg's status and the payout split. |
| After the last game | If the parlay died: one final update with every leg's status. |

A parlay is dead as soon as one leg misses. That miss is announced, and then the parlay goes quiet: its remaining legs are still graded, but nothing is posted until the last game in its window ends. Then one final update lists every leg's status. `/slip` shows the current state at any time.

Only games that kick off after a parlay's deadline can be picked for it. With the default Saturday 9:00 PM deadline that means Sunday's and Monday's games. The same limit applies to the commissioner's `/pickfor`. The cycle runs for the fantasy season only.

### Only during the fantasy season

Longshot runs only for the weeks of your fantasy season, which it reads from the Sleeper league:

- **First week:** the league's start week.
- **Last week:** the final week of the fantasy playoffs, worked out from the week the playoffs start and the number of rounds in the league's bracket. For a league whose playoffs start in Week 15 with three rounds, that is Week 17.
- **Before the draft and after the league is complete:** nothing runs.

### Who pays

| Week | Paid for by |
|---|---|
| The first week of the fantasy season | The commissioner (no fantasy week has been played yet) |
| Regular fantasy weeks | The lowest scorer of the week before |
| Fantasy playoff weeks | The commissioner |
| Thanksgiving bonus | The commissioner |

For a league that starts in Week 1 with playoffs in Weeks 15 to 17, the commissioner pays in Weeks 1, 15, 16 and 17 and the lowest scorer in Weeks 2 to 14. The playoff weeks come from Sleeper. In a commissioner week the announcement names no lowest scorer and no debt is recorded. `/admin loser` can still set one by hand.

Outside those weeks, and outside the NFL regular season (preseason, playoffs, offseason), no parlay is announced and nothing is posted. If the bot is restarted then with a parlay left unfinished, it settles that parlay's records without posting. Commands still answer when someone uses them. `/admin status` shows the weeks Sleeper reports.

### A new season

Sleeper gives a renewed league a new ID each season. If the connected league belongs to a different season from the one being played, no parlay runs, and Longshot messages the commissioner each week to run `/setup` with the new league ID. `/setup` refuses a league from an earlier season. Once it is redone, the week picks up from the next minute.

### Thanksgiving week

Thanksgiving week has two parlays, and each member makes one pick in each.

| Parlay | Games | Paid for by | Picks close |
|---|---|---|---|
| Thanksgiving bonus | Wednesday, Thursday and Friday | The commissioner | Tuesday 9:00 PM |
| The weekly parlay | Sunday and Monday | The previous week's lowest scorer | Saturday 9:00 PM |

- Both open with Tuesday's announcement. `/pick` puts a pick in whichever parlay its game belongs to.
- Each parlay has its own slip, reminders, slate updates, result and stats.
- The lowest scorer owes one stake, for the weekly parlay. The bonus parlay is not recorded as anyone's debt.
- `/placed`, `/grade` and `/admin run` take a `parlay` option that week to say which one is meant.
- The week is found from the schedule (the one containing Thanksgiving Day), not from its number.

Every other week, Christmas week included, has the one weekly parlay.

### Slates

Slates come from the real schedule: games that kick off within 90 minutes of each other. Thanksgiving's three games, Black Friday, Saturday games and London morning games each get their own slate and their own update.

## Commands

| Command | Who | What it does |
|---|---|---|
| `/pick who bet line` | Members | Lock in a leg. `who` is a player or a team; `line` lists the main line and every alternate. |
| `/mypick` | Members | Show your pick, or picks, for this week. |
| `/slip [week] [season]` | Members | The week's parlay (both, in Thanksgiving week) and how each leg is doing. |
| `/stats [member] [season]`, `/leaderboard [season]` | Members | Hit rate, streaks, parlays killed, weeks as lowest scorer, deadlines missed. |
| `/ledger [season]` | Members | Who owes, what has been collected, what has been won. |
| `/members` | Members | Which Sleeper team belongs to which Discord member. |
| `/setup league_id channel` | Commissioner | Connect the Sleeper league and choose the channel. |
| `/link member team`, `/unlink member` | Commissioner | Tie a Discord member to a Sleeper team. |
| `/config [setting] [value]` | Commissioner | Show or change the deadlines, sportsbook, announcement time, reminders, stake and line cache. |
| `/pickfor member who bet line` | Commissioner | Enter or replace a member's pick, including after the deadline, on any of that parlay's games that has not kicked off. |
| `/backfill member week game who bet odds [line]` | Commissioner | Record a pick after the fact, for a game that has already kicked off. See below. |
| `/placed odds [parlay]` | Commissioner | Record that the parlay was placed, with the sportsbook's odds. |
| `/paid member [week]` | Commissioner | Mark a stake as collected. |
| `/grade member result [week] [parlay]` | Commissioner | Settle a leg by hand, or correct one. |
| `/admin run step [week] [parlay]` | Commissioner | Run one step of the cycle now. |
| `/admin loser member [week]` | Commissioner | Set who pays, for a tie or a correction. |
| `/admin status` | Commissioner | How the bot is set up, this week's times, and Odds API credits left. |

`/slip`, `/stats`, `/leaderboard` and `/ledger` take an optional `season` (the year it started, such as `2026`) for looking back at an earlier year. Left empty, they show the current season. `/slip` with a season and no week shows that season's last parlay.

The commissioner is whoever ran `/setup`. Anyone with the server's Manage Server permission can also use the commissioner commands.

### Backfilling picks

`/backfill` records a pick for a game that has already kicked off: a week the parlay was run by hand before the bot, or a pick that never got entered.

- **You type the line and the price** from the bet slip, because the odds feed has no lines for games already played. The line is written as on the slip: `over 74.5`, `under 74.5`, `75+`, `-2.5`. Moneyline and anytime TD need no line. As you type, the field shows how it will be read.
- **The bot grades it** from ESPN's box score, the same way as any other leg.
- **Players** are offered from the game's box score as well as the current rosters, so someone who has since changed teams can still be found.
- **A week the bot never ran** is recorded as a finished parlay and is never posted about. It still counts in `/stats`, `/leaderboard` and `/slip`.
- **A parlay the bot is running** gets the leg added and announced like any late pick, including if it misses.
- **Entering a member's pick again** replaces it, and the parlay's result is worked out again.
- **It is not limited to the parlay's game window** and does not check for duplicate legs: it records what was actually bet.
- **For who owed the stake** in an earlier week, use `/admin loser member week`, then `/paid`.

For a game that has not kicked off, use `/pickfor`.

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
| Pick deadline | `sat 21:00` | Day and time, US Eastern. Applies to the current week if it has not closed. |
| Thanksgiving bonus deadline | `tue 21:00` | Day and time, US Eastern, for the Wednesday-to-Friday parlay in Thanksgiving week. |
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
