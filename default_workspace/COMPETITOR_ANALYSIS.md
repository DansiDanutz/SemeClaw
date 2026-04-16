# Competitor Intelligence Module

Automated YouTube competitor analysis for SemeClaw. Track competitor channels, analyze their content patterns, and receive weekly intelligence reports via Telegram.

## Features

- **Channel Tracking**: Add competitor YouTube channels to monitor
- **Automatic Data Collection**: Daily cron job fetches latest videos (title, views, tags, description, thumbnail)
- **AI Pattern Analysis**: Identifies what titles work, posting frequency, thumbnail styles, trending topics
- **Weekly Reports**: Automated competitor intelligence report sent to Telegram
- **Interactive Dashboard**: Web UI with competitor cards, upload calendar heatmap, title pattern analysis

## Setup

### 1. Database Setup

Run the SQL schema in your Supabase project:

```bash
# Copy the schema file and run it in Supabase SQL Editor
cat default_workspace/competitor_analysis_schema.sql
```

Or create the tables manually in Supabase:
- `yt_competitors` - tracked competitor channels
- `yt_competitor_videos` - video data from competitors
- `yt_competitor_reports` - generated analysis reports

### 2. Configuration

Add Supabase credentials to your config:

```yaml
# In your config.yaml or config.user.yaml
supabase_url: https://your-project.supabase.co
supabase_key: your-supabase-key
```

### 3. Install Dependencies

```bash
cd /Users/davidai/SemeClaw
uv sync  # or pip install -e .
```

### 4. Add Competitors

Use the agent tools to add competitor channels:

```
add_competitor(
    channel_url="https://youtube.com/@mkbhd",
    niche="tech reviews",
    notes="Top tech reviewer, good for thumbnail inspiration"
)
```

Or add directly to Supabase:

```sql
INSERT INTO yt_competitors (channel_url, channel_id, niche, enabled)
VALUES ('https://youtube.com/@mkbhd', 'UCBJycsmduvYEL83R_U4JriQ', 'tech reviews', true);
```

## Cron Jobs

Two cron jobs are pre-configured:

### Daily Competitor Fetch (`daily-competitor-fetch.yaml`)
- **Schedule**: 8:00 AM daily
- **What it does**: Fetches latest videos from all tracked channels, analyzes new content, logs findings

### Weekly Competitor Report (`weekly-competitor-report.yaml`)
- **Schedule**: 10:00 AM Monday
- **What it does**: Full weekly analysis with Telegram notification

## Agent Tools

| Tool | Description |
|------|-------------|
| `add_competitor` | Add a YouTube channel to track |
| `list_competitors` | List all tracked competitors |
| `fetch_competitor_videos` | Fetch latest videos using yt-dlp |
| `get_competitor_insights` | Get analyzed patterns and insights |
| `competitor_report` | Generate and send weekly report |

## Dashboard

Run the web dashboard:

```bash
python src/semeclaw/competitor_dashboard.py
# or
python src/semeclaw/competitor_dashboard.py 8766  # custom port
```

Open http://127.0.0.1:8766

### Dashboard Features
- **Competitor Cards**: Channel info, subscriber count, video count
- **Upload Calendar Heatmap**: GitHub-style grid showing upload activity
- **Title Pattern Analysis**: Bar chart of successful title formats
- **Thumbnail Styles**: Distribution of thumbnail approaches
- **Posting by Day**: Which days competitors post most
- **Top Videos**: Highest-performing videos with engagement rates
- **Trending Tags**: Most common tags across competitors

## Data Storage

### Supabase (preferred)
All data is stored in Supabase tables for persistence and querying.

### Local Fallback
If Supabase is not configured, data is saved locally:
- `workspace/data/competitors.json`
- `workspace/data/competitor_videos.json`
- `workspace/data/competitor_reports/`

## Title Pattern Classification

The module automatically classifies video titles into patterns:

| Pattern | Example |
|---------|---------|
| `how_to` | "How to Build a YouTube Bot" |
| `list` | "10 Best AI Tools for 2024" |
| `question` | "Is AI Taking Over Content Creation?" |
| `comparison` | "Claude vs GPT-4: Which is Better?" |
| `shocking` | "This AI Can Do WHAT?!" |
| `personal_experiment` | "I Spent 30 Days Using Only AI" |
| `tutorial` | "Beginner's Guide to AI Agents" |
| `review` | "Honest Review of the New Framework" |
| `announcement` | "Introducing Our New Feature" |
| `other` | Everything else |

## Thumbnail Style Classification

Thumbnail styles are inferred from URL patterns and title analysis:

| Style | Indicators |
|-------|------------|
| `face_reaction` | Reaction videos, person in thumbnail |
| `text_overlay` | Bold text on image |
| `before_after` | Transformation content |
| `split_comparison` | Side-by-side comparison |
| `custom` | Custom uploaded thumbnail |
| `default` | YouTube default thumbnail |
