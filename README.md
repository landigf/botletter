# Botletter

An adaptive, AI-generated daily science newsletter delivered to your phone via Telegram.
Around 7 minutes of reading each morning -- real arXiv papers explained clearly,
a deep curiosity topic, quick fascinating bites across fields, and a section
tied to your research focus. All for $0/day.

Built for researchers and students who want to stay broadly curious while keeping
a sharp eye on their own field.


## What it does

Every night at 11 PM, the system fetches fresh papers from arXiv, generates a
personalized newsletter using Gemini (free tier), and delivers it to your
Telegram chat. In the morning, when you open your laptop, a short reminder
pings your phone so you can read it on the commute.

You react to each section with inline buttons (love / meh / skip / more of this).
The system learns gently from your feedback -- shifting weights just enough to
surface more of what you care about, without collapsing into a filter bubble.
Exploration always wins long-term.

| Section | Description |
|---------|-------------|
| **Deep Curiosity** | One fascinating topic in depth -- physics, aerospace, bio, history of computing |
| **Research Spotlight** | A real arXiv paper explained, with researcher backstories |
| **Quick Bites** | Three mind-blowing facts from different fields |
| **Your Research Corner** | Tied to your thesis area, referencing real labs and ongoing debates |
| **Sunday Recap** | Weekly connections and emerging themes across everything you read |


## Make your own

This project is designed so anyone can fork it and run their own personalized
newsletter. The whole pipeline is free: Gemini free tier, arXiv open API,
Telegram bot API, macOS LaunchAgents.

### 1. Clone and install

```bash
git clone https://github.com/landigf/botletter.git
cd botletter
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Get your API keys (free)

You need two tokens, both free:

- **Gemini API key**: go to https://aistudio.google.com/apikey, sign in with
  Google, and create a key. The free tier gives you 1500 requests/day -- the
  newsletter uses about 5.
- **Telegram bot token**: open Telegram, search for `@BotFather`, send `/newbot`,
  follow the prompts, and copy the token it gives you.

Add both to your shell profile so they persist across sessions:

```bash
echo 'export GEMINI_API_KEY="your-key-here"' >> ~/.zshrc
echo 'export TELEGRAM_BOT_TOKEN="your-token-here"' >> ~/.zshrc
source ~/.zshrc
```

### 3. Configure your interests

Open `config.yaml` and make it yours (see `config.example.yaml` for a template
with a different field as reference). This is the most important step.

**reader** -- who you are:

```yaml
reader:
  name: "Your Name"
  background: "PhD Physics @ MIT (condensed matter)"
  thesis_area: "topological insulators and quantum transport"
  target_groups:
    - "Charlie Kane @ UPenn"
    - "Claudia Felser @ MPI Dresden"
```

**curiosity_themes** -- broad topics for the Deep Curiosity section. My
suggestion: put your research focus in here, but keep the list wide. Half the
value of a daily newsletter is stumbling into something you would never have
searched for. If you only track your own subfield, you lose the serendipity that
makes science fun. Include things you are curious about but would never allocate
time to read about on your own.

```yaml
curiosity_themes:
  - "how superconductors work at the atomic level"
  - "history of computing and technology"
  - "aerospace and space exploration"
  - "marine biology and deep ocean ecosystems"
  - "programming languages and compilers"
  - "cryptography and security"
```

**arxiv categories** -- which arXiv categories to pull papers from. Primary ones
are always queried; two random secondary ones are added each day for variety:

```yaml
arxiv:
  primary_categories:
    - "cond-mat.mes-hall"
    - "cond-mat.str-el"
    - "quant-ph"
  secondary_categories:
    - "cond-mat.supr-con"
    - "physics.app-ph"
    - "cs.AI"
```

**thesis_keywords** -- the system uses these to identify which fetched papers
are relevant to your specific research and routes them to the Research Corner:

```yaml
thesis_keywords:
  - "topological insulator"
  - "quantum Hall effect"
  - "spin-orbit coupling"
  - "Berry phase"
```

### 4. Register your Telegram chat

```bash
python main.py listen
```

Open Telegram, find your bot by the name you gave it in BotFather, and send
`/start`. You should see a confirmation. Press Ctrl+C to stop the listener.

### 5. Generate your first newsletter

```bash
python main.py generate
```

Check Telegram. Your newsletter should arrive in sections, each with reaction
buttons.

### 6. Automate it (macOS)

```bash
python scripts/install_schedule.py
```

This installs three lightweight macOS LaunchAgents:

- **Nightly generation** (11 PM) -- fetches your feedback, generates the
  newsletter, sends it. Also runs at login/wake as a fallback if your Mac was
  asleep at 11 PM. Idempotent: skips if already delivered, retries send-only
  if generated but not sent (e.g. no WiFi last night).
- **Command sync** (every 30 min) -- processes Telegram commands like
  `/add_interest` and replies. Single HTTP call, negligible battery.
- **Morning reminder** (at login/wake) -- pings Telegram so you see the
  newsletter on your phone.

No background processes. Zero battery impact when the Mac is asleep.

On Linux, you can replicate this with cron or systemd timers -- the CLI
commands are the same.


## Usage

```bash
python main.py generate              # Generate + send today's newsletter
python main.py generate --no-fetch   # LLM-only, skip arXiv
python main.py generate --no-send    # Generate markdown only, don't send
python main.py remind                # Send morning Telegram reminder
python main.py sync                  # Fetch and process pending Telegram commands
python main.py listen                # Start Telegram bot for /start registration
python main.py setup                 # Check what's configured
```

### Telegram commands

You can manage your interests directly from Telegram, without editing config
files. Casual input works -- the system uses Gemini to rephrase your message
into a clean config entry.

| Command | Example | What it does |
|---------|---------|--------------|
| `/add_interest` | `/add_interest that crazy quantum bio stuff` | Adds a curiosity theme |
| `/add_topic` | `/add_topic how GPUs talk to each other` | Adds a research keyword for arXiv filtering |
| `/add_researcher` | `/add_researcher that Italian prof at Stanford who made Spark` | Follows a researcher (LLM resolves the name) |
| `/remove_interest` | `/remove_interest materials science` | Removes a theme (fuzzy matching) |
| `/remove_topic` | `/remove_topic serverless` | Removes a keyword |
| `/remove_researcher` | `/remove_researcher Zaharia` | Removes a researcher |
| `/config` | `/config` | Shows all current settings |
| `/history` | `/history` | Lists past newsletter dates |

### Feedback buttons

Each section is delivered with inline reaction buttons:

- **Love it** -- gentle boost to this section type (+0.05 weight, max 1.15x)
- **Meh** -- slight decrease (-0.03, min 0.85x)
- **Skip** -- a bit more decrease (-0.05, min 0.85x)
- **More of this tomorrow** -- one-shot deep dive on the same topic next day

After the last section, a length feedback row: **Shorter / Perfect / Longer**.
Each vote shifts reading time by 0.3 minutes, clamped to +/-1.5 from your base.

The adaptation is intentionally gentle. A single "meh" does not kill a section.
The system is biased toward exploration -- weights always drift back toward 1.0
over time, so your newsletter never narrows down to a single topic.


## Architecture

```
config.yaml              Your interests, arXiv categories, thesis keywords
main.py                  CLI orchestrator: generate / remind / sync / listen / setup
fetcher.py               arXiv paper fetching (free, no auth)
generator.py             Newsletter generation via Gemini (google-genai SDK)
templates.py             LLM prompt templates with tone rotation
bot.py                   Telegram delivery, feedback collection, config commands
store.py                 Persistence: history, feedback, knowledge map, config editing
scripts/
  install_schedule.py    macOS LaunchAgent installer (3 agents)
data/
  history.json           Papers seen, themes used, quick bite topics
  feedback.json          Reactions, length preferences, adaptation weights
  knowledge-map.md       Growing log of every topic explored
  telegram.json          Bot state (chat ID, last topics)
  last_sent.txt          Idempotency guard for send retries
output/
  YYYY-MM-DD.md          Archived newsletters (one per day, searchable)
```


## How the feedback loop works

1. Before generating, the system fetches all pending Telegram updates (reactions,
   commands) from the last 24 hours. Telegram stores them server-side, so no
   persistent listener is needed.
2. Reactions update section weights in `feedback.json`. The adaptation formula
   uses small deltas (+0.05 for love, -0.03 for meh, -0.05 for skip), clamped
   to [0.85, 1.15]. This keeps the newsletter exploring broadly while still
   responding to your taste.
3. Length feedback shifts the target reading time by 0.3 minutes per vote,
   clamped to +/-1.5 minutes from your configured base (default: 7 minutes).
4. "More tomorrow" requests are stored and injected into the Research Corner
   prompt the next day, then marked as delivered (one-shot).
5. The knowledge map (`data/knowledge-map.md`) grows with every issue, tracking
   which topics, papers, and themes have been covered. The Sunday recap draws
   connections across the week.


## Cost

| Component | Cost |
|-----------|------|
| Gemini 2.5 Flash | Free tier (1500 req/day, newsletter uses ~5) |
| arXiv API | Free, no authentication |
| Telegram Bot API | Free |
| macOS LaunchAgent | Built-in |

Total: **$0/day**.


## Requirements

- Python 3.11+
- macOS (for LaunchAgent automation; the CLI works anywhere)
- A Google account (for Gemini API key)
- A Telegram account (for the bot)


## License

MIT
