"""
Competitor Analysis Dashboard — Flask server (port 8766)

Provides a web UI for the competitor intelligence module:
- Competitor cards with channel info
- Upload calendar heatmap (GitHub-style)
- Title pattern analysis
- Top performing videos
- Posting frequency trends

Run:
  python src/semeclaw/competitor_dashboard.py

Or:
  flask --app src/semeclaw/competitor_dashboard:app run --port 8766
"""

import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

logger = logging.getLogger("competitor_dashboard")

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------
WORKSPACE = Path(os.environ.get("SEMECLAW_WORKSPACE", "default_workspace"))
DATA_DIR = WORKSPACE / "data"
COMPETITORS_FILE = DATA_DIR / "competitors.json"
VIDEOS_FILE = DATA_DIR / "competitor_videos.json"
REPORTS_DIR = DATA_DIR / "competitor_reports"

# Try Supabase if configured
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


def get_supabase_client():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        from supabase import create_client

        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except ImportError:
        return None


def load_competitors():
    """Load competitor data from Supabase or local file."""
    client = get_supabase_client()
    if client:
        try:
            result = client.table("yt_competitors").select("*").execute()
            return result.data
        except Exception as e:
            logger.error(f"Supabase error: {e}")

    if COMPETITORS_FILE.exists():
        return json.loads(COMPETITORS_FILE.read_text())
    return []


def load_videos(days=90):
    """Load video data from Supabase or local file."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    client = get_supabase_client()
    if client:
        try:
            result = (
                client.table("yt_competitor_videos")
                .select("*")
                .gte("published_at", cutoff)
                .order("published_at", desc=True)
                .execute()
            )
            return result.data
        except Exception as e:
            logger.error(f"Supabase error: {e}")

    if VIDEOS_FILE.exists():
        videos = json.loads(VIDEOS_FILE.read_text())
        # Filter by date
        return [v for v in videos if v.get("published_at", "") >= cutoff]
    return []


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Flask(__name__)

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------


@app.route("/api/competitors")
def api_competitors():
    """Get all tracked competitors."""
    competitors = load_competitors()
    return jsonify(competitors)


@app.route("/api/videos")
def api_videos():
    """Get recent videos, optionally filtered by competitor."""
    days = request.args.get("days", 90, type=int)
    competitor_id = request.args.get("competitor_id")

    videos = load_videos(days)
    if competitor_id:
        videos = [v for v in videos if v.get("competitor_id") == competitor_id]

    return jsonify(videos)


@app.route("/api/insights")
def api_insights():
    """Get analyzed insights for the dashboard."""
    days = request.args.get("days", 90, type=int)
    videos = load_videos(days)
    competitors = load_competitors()

    if not videos:
        return jsonify(
            {
                "total_videos": 0,
                "total_competitors": len(competitors),
                "title_patterns": {},
                "thumbnail_styles": {},
                "posting_by_day": {},
                "top_videos": [],
                "avg_engagement_by_pattern": {},
                "trending_tags": [],
                "competitor_activity": {},
            }
        )

    # Title patterns
    title_patterns = Counter(v.get("title_pattern", "other") for v in videos)

    # Thumbnail styles
    thumbnail_styles = Counter(v.get("thumbnail_style", "unknown") for v in videos)

    # Posting by day of week
    day_counts = Counter()
    for v in videos:
        if v.get("published_at"):
            try:
                if isinstance(v["published_at"], str):
                    dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
                else:
                    dt = datetime.fromtimestamp(v["published_at"])
                day_counts[dt.strftime("%A")] += 1
            except (ValueError, TypeError):
                pass

    # Top videos
    top_videos = sorted(videos, key=lambda v: v.get("view_count", 0), reverse=True)[:15]

    # Engagement by pattern
    engagement_by_pattern = defaultdict(list)
    for v in videos:
        pattern = v.get("title_pattern", "other")
        eng = v.get("engagement_rate", 0)
        if eng:
            engagement_by_pattern[pattern].append(eng)

    avg_engagement = {}
    for pattern, engs in engagement_by_pattern.items():
        avg_engagement[pattern] = round(sum(engs) / len(engs), 2)

    # Trending tags
    all_tags = []
    for v in videos:
        all_tags.extend(v.get("tags", [])[:5])
    top_tags = [{"tag": t, "count": c} for t, c in Counter(all_tags).most_common(20)]

    # Activity by competitor
    by_competitor = defaultdict(list)
    for v in videos:
        by_competitor[v.get("competitor_id", "unknown")].append(v)

    competitor_activity = {}
    for comp_id, vids in by_competitor.items():
        name = comp_id
        for c in competitors:
            if c.get("id") == comp_id or c.get("channel_id") == comp_id:
                name = c.get("channel_name", comp_id)
                break
        competitor_activity[name] = len(vids)

    return jsonify(
        {
            "total_videos": len(videos),
            "total_competitors": len(competitors),
            "title_patterns": dict(title_patterns),
            "thumbnail_styles": dict(thumbnail_styles),
            "posting_by_day": dict(day_counts),
            "top_videos": [
                {
                    "title": v.get("title", ""),
                    "views": v.get("view_count", 0),
                    "engagement_rate": v.get("engagement_rate", 0),
                    "published_at": v.get("published_at", ""),
                    "thumbnail_url": v.get("thumbnail_url", ""),
                    "competitor_id": v.get("competitor_id", ""),
                }
                for v in top_videos
            ],
            "avg_engagement_by_pattern": avg_engagement,
            "trending_tags": top_tags,
            "competitor_activity": competitor_activity,
        }
    )


@app.route("/api/heatmap")
def api_heatmap():
    """Get upload heatmap data (date -> count) for the last 90 days."""
    videos = load_videos(90)
    competitors = load_competitors()

    # Build date -> count map
    heatmap = {}
    for v in videos:
        if v.get("published_at"):
            try:
                if isinstance(v["published_at"], str):
                    dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
                else:
                    dt = datetime.fromtimestamp(v["published_at"])
                date_str = dt.strftime("%Y-%m-%d")
                heatmap[date_str] = heatmap.get(date_str, 0) + 1
            except (ValueError, TypeError):
                pass

    return jsonify(heatmap)


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Competitor Intelligence Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-primary: #0f172a;
            --bg-secondary: #1e293b;
            --bg-card: #1e293b;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent: #3b82f6;
            --accent-hover: #2563eb;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --border: #334155;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
        }

        .header {
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .header h1 {
            font-size: 1.5rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .header .subtitle {
            color: var(--text-secondary);
            font-size: 0.875rem;
        }

        .controls {
            display: flex;
            gap: 1rem;
            align-items: center;
        }

        .controls select, .controls button {
            background: var(--bg-primary);
            color: var(--text-primary);
            border: 1px solid var(--border);
            padding: 0.5rem 1rem;
            border-radius: 0.5rem;
            cursor: pointer;
        }

        .controls button {
            background: var(--accent);
            border: none;
        }

        .controls button:hover {
            background: var(--accent-hover);
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 1.5rem;
        }

        .stats-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 1.5rem;
        }

        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 0.75rem;
            padding: 1.25rem;
        }

        .stat-card .label {
            color: var(--text-secondary);
            font-size: 0.875rem;
            margin-bottom: 0.5rem;
        }

        .stat-card .value {
            font-size: 2rem;
            font-weight: 700;
        }

        .grid {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 1.5rem;
            margin-bottom: 1.5rem;
        }

        @media (max-width: 1024px) {
            .grid { grid-template-columns: 1fr; }
        }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 0.75rem;
            padding: 1.25rem;
        }

        .card h3 {
            font-size: 1rem;
            margin-bottom: 1rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        /* Competitor Cards */
        .competitor-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 1rem;
        }

        .competitor-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 0.75rem;
            padding: 1rem;
            transition: transform 0.2s, border-color 0.2s;
        }

        .competitor-card:hover {
            transform: translateY(-2px);
            border-color: var(--accent);
        }

        .competitor-card .name {
            font-weight: 600;
            font-size: 1.1rem;
            margin-bottom: 0.25rem;
        }

        .competitor-card .url {
            color: var(--accent);
            font-size: 0.8rem;
            word-break: break-all;
        }

        .competitor-card .niche {
            display: inline-block;
            background: var(--bg-primary);
            padding: 0.25rem 0.5rem;
            border-radius: 0.25rem;
            font-size: 0.75rem;
            margin-top: 0.5rem;
        }

        .competitor-card .stats {
            display: flex;
            gap: 1rem;
            margin-top: 0.75rem;
            font-size: 0.875rem;
            color: var(--text-secondary);
        }

        .competitor-card .video-count {
            color: var(--success);
            font-weight: 600;
        }

        /* Heatmap */
        .heatmap-container {
            overflow-x: auto;
        }

        .heatmap {
            display: grid;
            grid-template-rows: repeat(7, 12px);
            grid-auto-flow: column;
            gap: 2px;
        }

        .heatmap-cell {
            width: 12px;
            height: 12px;
            border-radius: 2px;
            background: var(--bg-primary);
        }

        .heatmap-cell.level-1 { background: #1e40af; }
        .heatmap-cell.level-2 { background: #2563eb; }
        .heatmap-cell.level-3 { background: #3b82f6; }
        .heatmap-cell.level-4 { background: #60a5fa; }

        /* Pattern bars */
        .pattern-bar {
            display: flex;
            align-items: center;
            margin-bottom: 0.75rem;
        }

        .pattern-bar .label {
            width: 140px;
            font-size: 0.875rem;
            text-transform: capitalize;
        }

        .pattern-bar .bar-bg {
            flex: 1;
            height: 20px;
            background: var(--bg-primary);
            border-radius: 4px;
            overflow: hidden;
        }

        .pattern-bar .bar-fill {
            height: 100%;
            background: var(--accent);
            border-radius: 4px;
            transition: width 0.3s;
        }

        .pattern-bar .count {
            width: 50px;
            text-align: right;
            font-size: 0.875rem;
            color: var(--text-secondary);
        }

        /* Top videos table */
        .video-table {
            width: 100%;
            border-collapse: collapse;
        }

        .video-table th, .video-table td {
            padding: 0.75rem;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }

        .video-table th {
            color: var(--text-secondary);
            font-weight: 500;
            font-size: 0.875rem;
        }

        .video-table td {
            font-size: 0.875rem;
        }

        .video-table .title {
            max-width: 400px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .video-table .views {
            color: var(--success);
            font-weight: 600;
        }

        .video-table .engagement {
            color: var(--warning);
        }

        /* Tags */
        .tag-cloud {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
        }

        .tag {
            background: var(--bg-primary);
            border: 1px solid var(--border);
            padding: 0.25rem 0.75rem;
            border-radius: 999px;
            font-size: 0.8rem;
        }

        .tag .count {
            color: var(--accent);
            margin-left: 0.25rem;
        }

        /* Chart containers */
        .chart-container {
            position: relative;
            height: 250px;
        }

        .loading {
            text-align: center;
            padding: 3rem;
            color: var(--text-secondary);
        }

        .refresh-indicator {
            position: fixed;
            bottom: 1rem;
            right: 1rem;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            padding: 0.5rem 1rem;
            border-radius: 0.5rem;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>🕵️ Competitor Intelligence</h1>
            <div class="subtitle">YouTube competitor analysis dashboard</div>
        </div>
        <div class="controls">
            <select id="daysFilter">
                <option value="7">Last 7 days</option>
                <option value="30" selected>Last 30 days</option>
                <option value="90">Last 90 days</option>
            </select>
            <button onclick="refreshData()">↻ Refresh</button>
        </div>
    </div>

    <div class="container">
        <!-- Stats Row -->
        <div class="stats-row" id="statsRow">
            <div class="stat-card">
                <div class="label">Tracked Competitors</div>
                <div class="value" id="statCompetitors">—</div>
            </div>
            <div class="stat-card">
                <div class="label">Videos Analyzed</div>
                <div class="value" id="statVideos">—</div>
            </div>
            <div class="stat-card">
                <div class="label">Avg Engagement Rate</div>
                <div class="value" id="statEngagement">—</div>
            </div>
            <div class="stat-card">
                <div class="label">Top Title Pattern</div>
                <div class="value" id="statTopPattern">—</div>
            </div>
        </div>

        <!-- Competitor Cards -->
        <div class="card" style="margin-bottom: 1.5rem;">
            <h3>📺 Tracked Competitors</h3>
            <div class="competitor-grid" id="competitorGrid">
                <div class="loading">Loading competitors...</div>
            </div>
        </div>

        <!-- Main Grid -->
        <div class="grid">
            <!-- Left Column -->
            <div>
                <!-- Upload Calendar Heatmap -->
                <div class="card" style="margin-bottom: 1.5rem;">
                    <h3>📅 Upload Calendar (Last 90 Days)</h3>
                    <div class="heatmap-container">
                        <div class="heatmap" id="heatmap"></div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.5rem; margin-top: 0.5rem; font-size: 0.75rem; color: var(--text-secondary);">
                        <span>Less</span>
                        <div style="width: 12px; height: 12px; background: var(--bg-primary); border-radius: 2px;"></div>
                        <div style="width: 12px; height: 12px; background: #1e40af; border-radius: 2px;"></div>
                        <div style="width: 12px; height: 12px; background: #2563eb; border-radius: 2px;"></div>
                        <div style="width: 12px; height: 12px; background: #3b82f6; border-radius: 2px;"></div>
                        <div style="width: 12px; height: 12px; background: #60a5fa; border-radius: 2px;"></div>
                        <span>More</span>
                    </div>
                </div>

                <!-- Top Videos -->
                <div class="card">
                    <h3>🔥 Top Performing Videos</h3>
                    <div style="overflow-x: auto;">
                        <table class="video-table">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Title</th>
                                    <th>Views</th>
                                    <th>Engagement</th>
                                    <th>Date</th>
                                </tr>
                            </thead>
                            <tbody id="topVideosBody">
                                <tr><td colspan="5" class="loading">Loading...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Right Column -->
            <div>
                <!-- Title Pattern Analysis -->
                <div class="card" style="margin-bottom: 1.5rem;">
                    <h3>📊 Title Pattern Analysis</h3>
                    <div id="titlePatterns"></div>
                </div>

                <!-- Thumbnail Styles -->
                <div class="card" style="margin-bottom: 1.5rem;">
                    <h3>🖼️ Thumbnail Styles</h3>
                    <div id="thumbnailStyles"></div>
                </div>

                <!-- Posting by Day -->
                <div class="card" style="margin-bottom: 1.5rem;">
                    <h3>📅 Posting by Day</h3>
                    <div class="chart-container">
                        <canvas id="dayChart"></canvas>
                    </div>
                </div>

                <!-- Trending Tags -->
                <div class="card">
                    <h3>🏷️ Trending Tags</h3>
                    <div class="tag-cloud" id="tagCloud"></div>
                </div>
            </div>
        </div>
    </div>

    <div class="refresh-indicator" id="refreshIndicator">Auto-refreshing every 60s</div>

    <script>
        const PATTERN_EMOJIS = {
            how_to: '📖', list: '📋', question: '❓',
            comparison: '⚖️', shocking: '😱', tutorial: '🎓',
            review: '⭐', personal_experiment: '🧪', secret: '🤫',
            announcement: '📢', other: '📌'
        };

        let dayChart = null;

        async function loadJSON(url) {
            const resp = await fetch(url);
            return resp.json();
        }

        async function refreshData() {
            const days = document.getElementById('daysFilter').value;

            try {
                const [competitors, insights, heatmap] = await Promise.all([
                    loadJSON('/api/competitors'),
                    loadJSON(`/api/insights?days=${days}`),
                    loadJSON('/api/heatmap')
                ]);

                renderStats(insights);
                renderCompetitors(competitors, insights.competitor_activity);
                renderHeatmap(heatmap);
                renderTitlePatterns(insights.title_patterns, insights.avg_engagement_by_pattern);
                renderThumbnailStyles(insights.thumbnail_styles);
                renderTopVideos(insights.top_videos, competitors);
                renderDayChart(insights.posting_by_day);
                renderTags(insights.trending_tags);

                document.getElementById('refreshIndicator').textContent =
                    `Updated: ${new Date().toLocaleTimeString()}`;
            } catch (e) {
                console.error('Failed to load data:', e);
            }
        }

        function renderStats(insights) {
            document.getElementById('statCompetitors').textContent = insights.total_competitors;
            document.getElementById('statVideos').textContent = insights.total_videos.toLocaleString();

            const patterns = insights.title_patterns;
            if (patterns && Object.keys(patterns).length > 0) {
                const top = Object.entries(patterns).sort((a, b) => b[1] - a[1])[0];
                document.getElementById('statTopPattern').textContent =
                    `${PATTERN_EMOJIS[top[0]] || ''} ${top[0].replace('_', ' ')}`;
            }

            const engagements = Object.values(insights.avg_engagement_by_pattern);
            if (engagements.length > 0) {
                const avg = engagements.reduce((a, b) => a + b, 0) / engagements.length;
                document.getElementById('statEngagement').textContent = `${avg.toFixed(1)}%`;
            }
        }

        function renderCompetitors(competitors, activity) {
            const grid = document.getElementById('competitorGrid');
            if (!competitors.length) {
                grid.innerHTML = '<div class="loading">No competitors tracked yet. Use the add_competitor tool to start.</div>';
                return;
            }

            grid.innerHTML = competitors.map(c => {
                const vidCount = activity[c.channel_name] || activity[c.id] || activity[c.channel_id] || 0;
                return `
                    <div class="competitor-card">
                        <div class="name">${c.channel_name || c.channel_id || 'Unknown'}</div>
                        <div class="url">${c.channel_url}</div>
                        ${c.niche ? `<div class="niche">${c.niche}</div>` : ''}
                        <div class="stats">
                            <span>Subscribers: ${formatNumber(c.subscriber_count)}</span>
                            <span class="video-count">${vidCount} videos tracked</span>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function renderHeatmap(heatmap) {
            const container = document.getElementById('heatmap');
            const cells = [];
            const today = new Date();

            for (let i = 89; i >= 0; i--) {
                const date = new Date(today);
                date.setDate(date.getDate() - i);
                const dateStr = date.toISOString().split('T')[0];
                const count = heatmap[dateStr] || 0;

                let level = '';
                if (count >= 4) level = 'level-4';
                else if (count >= 3) level = 'level-3';
                else if (count >= 2) level = 'level-2';
                else if (count >= 1) level = 'level-1';

                cells.push(`<div class="heatmap-cell ${level}" title="${dateStr}: ${count} uploads"></div>`);
            }

            container.innerHTML = cells.join('');
        }

        function renderTitlePatterns(patterns, engagements) {
            const container = document.getElementById('titlePatterns');
            const entries = Object.entries(patterns).sort((a, b) => b[1] - a[1]);
            const max = entries.length > 0 ? entries[0][1] : 1;

            container.innerHTML = entries.map(([pattern, count]) => {
                const emoji = PATTERN_EMOJIS[pattern] || '📌';
                const eng = engagements[pattern] || 0;
                const width = (count / max * 100).toFixed(1);
                return `
                    <div class="pattern-bar">
                        <div class="label">${emoji} ${pattern.replace('_', ' ')}</div>
                        <div class="bar-bg"><div class="bar-fill" style="width: ${width}%"></div></div>
                        <div class="count">${count}</div>
                    </div>
                `;
            }).join('');
        }

        function renderThumbnailStyles(styles) {
            const container = document.getElementById('thumbnailStyles');
            const entries = Object.entries(styles).sort((a, b) => b[1] - a[1]);

            container.innerHTML = entries.map(([style, count]) => `
                <div class="pattern-bar">
                    <div class="label">${style.replace('_', ' ')}</div>
                    <div class="bar-bg"><div class="bar-fill" style="width: ${count / entries[0][1] * 100}%"></div></div>
                    <div class="count">${count}</div>
                </div>
            `).join('');
        }

        function renderTopVideos(videos, competitors) {
            const tbody = document.getElementById('topVideosBody');
            if (!videos.length) {
                tbody.innerHTML = '<tr><td colspan="5" class="loading">No video data available</td></tr>';
                return;
            }

            tbody.innerHTML = videos.map((v, i) => {
                let compName = '';
                for (const c of competitors) {
                    if (c.id === v.competitor_id || c.channel_id === v.competitor_id) {
                        compName = c.channel_name || c.channel_id;
                        break;
                    }
                }
                return `
                    <tr>
                        <td>${i + 1}</td>
                        <td class="title" title="${v.title}">${v.title}</td>
                        <td class="views">${formatNumber(v.views)}</td>
                        <td class="engagement">${v.engagement_rate}%</td>
                        <td>${formatDate(v.published_at)}</td>
                    </tr>
                `;
            }).join('');
        }

        function renderDayChart(dayData) {
            const ctx = document.getElementById('dayChart').getContext('2d');
            if (dayChart) dayChart.destroy();

            const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
            const data = days.map(d => dayData[d] || 0);

            dayChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: days.map(d => d.slice(0, 3)),
                    datasets: [{
                        label: 'Uploads',
                        data: data,
                        backgroundColor: '#3b82f6',
                        borderRadius: 4,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: { beginAtZero: true, grid: { color: '#334155' }, ticks: { color: '#94a3b8' } },
                        x: { grid: { display: false }, ticks: { color: '#94a3b8' } }
                    }
                }
            });
        }

        function renderTags(tags) {
            const container = document.getElementById('tagCloud');
            container.innerHTML = tags.slice(0, 25).map(t =>
                `<span class="tag">${t.tag}<span class="count">${t.count}</span></span>`
            ).join('');
        }

        function formatNumber(n) {
            if (!n) return '0';
            if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
            if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
            return n.toString();
        }

        function formatDate(dateStr) {
            if (!dateStr) return '—';
            try {
                const d = new Date(dateStr);
                return d.toLocaleDateString();
            } catch {
                return dateStr;
            }
        }

        // Initial load
        refreshData();

        // Auto-refresh every 60 seconds
        setInterval(refreshData, 60000);
    </script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    """Serve the competitor intelligence dashboard."""
    return render_template_string(DASHBOARD_HTML)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    print(f"🕵️  Competitor Intelligence Dashboard → http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
