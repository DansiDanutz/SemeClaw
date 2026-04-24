"""Competitor intelligence tools for YouTube channel analysis.

Powers the competitor analysis module:
- add_competitor: Add a competitor YouTube channel to track
- fetch_competitor_videos: Fetch latest videos from a competitor using yt-dlp
- get_competitor_insights: Get AI-analyzed patterns (titles, posting frequency, thumbnails)
- get_competitor_report: Generate weekly competitor intelligence report
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from semeclaw.tools.base import BaseTool

logger = logging.getLogger(__name__)


def _get_supabase_client(config: Any):
    """Get Supabase client from config."""
    try:
        from supabase import create_client

        supabase_url = getattr(config, "supabase_url", None) or getattr(config, "SUPABASE_URL", None)
        supabase_key = getattr(config, "supabase_key", None) or getattr(config, "SUPABASE_KEY", None)

        if not supabase_url or not supabase_key:
            return None

        return create_client(supabase_url, supabase_key)
    except ImportError:
        return None


def _extract_channel_id_from_url(url: str) -> str | None:
    """Extract channel ID or handle from a YouTube URL."""
    patterns = [
        r"youtube\.com/channel/([UC\w-]+)",
        r"youtube\.com/@([\w-]+)",
        r"youtube\.com/c/([\w-]+)",
        r"youtube\.com/user/([\w-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _classify_title_pattern(title: str) -> str:
    """Classify a video title into a pattern category."""
    title_lower = title.lower()

    if title_lower.startswith(("how to", "how i", "how to make", "how to build")):
        return "how_to"
    if re.search(r"^\d+[\s.:-]|^top \d+|^first \d+", title_lower):
        return "list"
    if title_lower.endswith("?") or title_lower.startswith(("what", "why", "when", "can", "is ", "are ", "do ")):
        return "question"
    if any(w in title_lower for w in ("vs", "versus", "compared to", "comparison", "or")):
        return "comparison"
    if any(w in title_lower for w in ("shocking", "insane", "crazy", "unbelievable", "omg", "wtf")):
        return "shocking"
    if any(w in title_lower for w in ("secret", "hidden", "nobody knows", "they don't want")):
        return "secret"
    if any(w in title_lower for w in ("i tried", "i tested", "i spent", "i made")):
        return "personal_experiment"
    if any(w in title_lower for w in ("tutorial", "guide", "beginner", "step by step")):
        return "tutorial"
    if any(w in title_lower for w in ("review", "tested", "honest review", "worth it")):
        return "review"
    if any(w in title_lower for w in ("announcement", "launching", "new ", "introducing")):
        return "announcement"

    return "other"


def _classify_thumbnail_style(thumbnail_url: str | None, title: str = "") -> str:
    """Classify thumbnail style based on URL patterns and title analysis."""
    if not thumbnail_url:
        return "unknown"

    url_lower = thumbnail_url.lower()

    # YouTube default thumbnails
    if "default" in url_lower or "mqdefault" in url_lower:
        return "default"

    # Common thumbnail patterns based on filename
    if "hqdefault" in url_lower:
        return "hq_default"
    if "maxresdefault" in url_lower:
        return "high_res"

    # Infer from title patterns when we can't see the actual image
    title_lower = title.lower()
    if any(w in title_lower for w in ("vs", "versus", "comparison")):
        return "split_comparison"
    if any(w in title_lower for w in ("before", "after", "transformation")):
        return "before_after"
    if any(w in title_lower for w in ("react", "reaction")):
        return "face_reaction"
    if re.search(r"\d+\+|\$\d+|free|cheap", title_lower):
        return "text_overlay"

    return "custom"


# ── Tool Classes ──────────────────────────────────────────────────────────────


class AddCompetitorTool(BaseTool):
    """Add a competitor YouTube channel to the tracking list."""

    def __init__(self, config: Any):
        super().__init__(
            name="add_competitor",
            description=(
                "Add a YouTube channel to the competitor tracking list. "
                "Provide the channel URL (youtube.com/@handle, youtube.com/channel/ID, etc.) "
                "and optional niche/tags. The channel will be fetched daily."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel_url": {
                        "type": "string",
                        "description": "YouTube channel URL (e.g., https://youtube.com/@mkbhd)",
                    },
                    "niche": {
                        "type": "string",
                        "description": "The channel's niche (e.g., 'tech reviews', 'cooking', 'finance')",
                    },
                    "custom_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Custom tags for categorization",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Any notes about why this competitor is important",
                    },
                },
                "required": ["channel_url"],
            },
        )
        self.config = config

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        client = _get_supabase_client(self.config)
        channel_url = args["channel_url"]

        channel_id = _extract_channel_id_from_url(channel_url)
        if not channel_id:
            return f"Error: Could not extract channel ID from URL: {channel_url}"

        competitor_data = {
            "channel_url": channel_url,
            "channel_id": channel_id,
            "niche": args.get("niche", ""),
            "custom_tags": args.get("custom_tags", []),
            "notes": args.get("notes", ""),
            "enabled": True,
        }

        if client:
            try:
                result = client.table("yt_competitors").upsert(competitor_data, on_conflict="channel_url").execute()
                return f"✓ Added competitor: {channel_id} ({args.get('niche', 'no niche')})"
            except Exception as e:
                logger.error(f"Supabase error adding competitor: {e}")
                return f"Error saving to database: {e}"

        # Fallback: save to local file
        workspace = session.agent.config.workspace
        competitors_file = workspace / "data" / "competitors.json"
        competitors_file.parent.mkdir(parents=True, exist_ok=True)

        competitors = []
        if competitors_file.exists():
            competitors = json.loads(competitors_file.read_text())

        # Check for duplicate
        for c in competitors:
            if c.get("channel_url") == channel_url:
                return f"Competitor already tracked: {channel_url}"

        competitor_data["id"] = channel_id
        competitor_data["created_at"] = datetime.now().isoformat()
        competitors.append(competitor_data)
        competitors_file.write_text(json.dumps(competitors, indent=2))

        return f"✓ Added competitor: {channel_id} ({args.get('niche', 'no niche')}) [saved locally]"


class ListCompetitorsTool(BaseTool):
    """List all tracked competitor channels."""

    def __init__(self, config: Any):
        super().__init__(
            name="list_competitors",
            description="List all YouTube channels being tracked as competitors.",
            parameters={
                "type": "object",
                "properties": {
                    "niche": {
                        "type": "string",
                        "description": "Filter by niche (optional)",
                    },
                },
            },
        )
        self.config = config

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        client = _get_supabase_client(self.config)
        niche = args.get("niche")

        if client:
            try:
                query = client.table("yt_competitors").select("*").eq("enabled", True)
                if niche:
                    query = query.eq("niche", niche)
                result = query.execute()
                competitors = result.data
            except Exception as e:
                logger.error(f"Supabase error listing competitors: {e}")
                competitors = []
        else:
            workspace = session.agent.config.workspace
            competitors_file = workspace / "data" / "competitors.json"
            if competitors_file.exists():
                competitors = json.loads(competitors_file.read_text())
            else:
                competitors = []

        if not competitors:
            return "No competitors tracked. Use add_competitor to start tracking channels."

        lines = [f"## Tracked Competitors ({len(competitors)})\n"]
        for c in competitors:
            name = c.get("channel_name", c.get("channel_id", "Unknown"))
            niche = c.get("niche", "—")
            subs = c.get("subscriber_count", "—")
            last_fetch = c.get("last_fetched_at", "never")
            lines.append(f"- **{name}** ({niche})")
            lines.append(f"  {c['channel_url']} | Subs: {subs} | Last fetch: {last_fetch}")

        return "\n".join(lines)


class FetchCompetitorVideosTool(BaseTool):
    """Fetch latest videos from competitor channels using yt-dlp."""

    def __init__(self, config: Any):
        super().__init__(
            name="fetch_competitor_videos",
            description=(
                "Fetch the latest videos from tracked competitor channels using yt-dlp. "
                "Extracts title, views, tags, description, thumbnail URL, and publish date. "
                "Optionally specify a competitor URL or fetch all tracked channels."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel_url": {
                        "type": "string",
                        "description": "Specific channel URL to fetch (optional — fetches all if not provided)",
                    },
                    "max_videos": {
                        "type": "integer",
                        "description": "Max videos to fetch per channel (default: 10)",
                    },
                },
            },
        )
        self.config = config

    def _get_competitor_urls(self, workspace: Path, channel_url: str | None) -> list[str]:
        """Get list of competitor URLs to fetch."""
        if channel_url:
            return [channel_url]

        client = _get_supabase_client(self.config)
        if client:
            try:
                result = client.table("yt_competitors").select("channel_url").eq("enabled", True).execute()
                return [r["channel_url"] for r in result.data]
            except Exception:
                pass

        competitors_file = workspace / "data" / "competitors.json"
        if competitors_file.exists():
            competitors = json.loads(competitors_file.read_text())
            return [c["channel_url"] for c in competitors if c.get("enabled", True)]

        return []

    def _fetch_channel_info(self, channel_url: str) -> dict:
        """Fetch channel metadata using yt-dlp."""
        try:
            import yt_dlp

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "skip_download": True,
                "playlistend": 1,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(channel_url, download=False)
                if not info:
                    return {}

                return {
                    "channel_id": info.get("channel_id", ""),
                    "channel_name": info.get("channel", ""),
                    "channel_description": info.get("description", ""),
                }
        except Exception as e:
            logger.error(f"Error fetching channel info: {e}")
            return {}

    def _fetch_videos(self, channel_url: str, max_videos: int = 10) -> list[dict]:
        """Fetch latest videos from a channel using yt-dlp."""
        try:
            import yt_dlp

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": "in_playlist",
                "skip_download": True,
                "playlistend": max_videos,
                "sleep_interval": 1,
                "max_sleep_interval": 3,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"{channel_url}/videos", download=False)

                if not info or "entries" not in info:
                    return []

                videos = []
                for entry in info.get("entries", [])[:max_videos]:
                    if not entry:
                        continue

                    video_data = {
                        "video_id": entry.get("id", ""),
                        "title": entry.get("title", ""),
                        "description": entry.get("description", "") or "",
                        "published_at": entry.get("timestamp"),
                        "view_count": entry.get("view_count", 0),
                        "like_count": entry.get("like_count", 0),
                        "comment_count": entry.get("comment_count", 0),
                        "duration_seconds": entry.get("duration", 0),
                        "tags": entry.get("tags", []) or [],
                        "thumbnail_url": entry.get("thumbnail", ""),
                    }

                    # Convert timestamp to ISO format
                    if video_data["published_at"]:
                        video_data["published_at"] = datetime.fromtimestamp(video_data["published_at"]).isoformat()
                    else:
                        video_data["published_at"] = None

                    # Classify patterns
                    video_data["title_pattern"] = _classify_title_pattern(video_data["title"])
                    video_data["thumbnail_style"] = _classify_thumbnail_style(
                        video_data["thumbnail_url"], video_data["title"]
                    )

                    # Calculate engagement rate
                    views = video_data["view_count"] or 1
                    likes = video_data["like_count"] or 0
                    comments = video_data["comment_count"] or 0
                    video_data["engagement_rate"] = round((likes + comments) / views * 100, 2)

                    videos.append(video_data)

                return videos

        except Exception as e:
            logger.error(f"Error fetching videos from {channel_url}: {e}")
            return []

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        workspace = session.agent.config.workspace
        channel_url = args.get("channel_url")
        max_videos = args.get("max_videos", 10)

        urls = self._get_competitor_urls(workspace, channel_url)
        if not urls:
            return "No competitor channels found. Use add_competitor first."

        all_results = []
        for url in urls:
            channel_info = self._fetch_channel_info(url)
            videos = self._fetch_videos(url, max_videos)

            # Update channel info in Supabase
            client = _get_supabase_client(self.config)
            if client and channel_info:
                try:
                    client.table("yt_competitors").update(
                        {
                            **channel_info,
                            "last_fetched_at": datetime.now().isoformat(),
                        }
                    ).eq("channel_url", url).execute()
                except Exception as e:
                    logger.error(f"Error updating channel info: {e}")

            # Save videos
            if client:
                for video in videos:
                    try:
                        # Get competitor_id
                        comp_result = client.table("yt_competitors").select("id").eq("channel_url", url).execute()
                        if comp_result.data:
                            video["competitor_id"] = comp_result.data[0]["id"]
                            client.table("yt_competitor_videos").upsert(
                                video, on_conflict="competitor_id,video_id"
                            ).execute()
                    except Exception as e:
                        logger.error(f"Error saving video: {e}")

            all_results.append(
                {
                    "channel": channel_info.get("channel_name", url),
                    "videos_found": len(videos),
                }
            )

        # Format results
        lines = ["## Competitor Video Fetch Results\n"]
        for r in all_results:
            lines.append(f"- **{r['channel']}**: {r['videos_found']} videos fetched")

        total = sum(r["videos_found"] for r in all_results)
        lines.append(f"\n**Total: {total} videos from {len(all_results)} channels**")

        return "\n".join(lines)


class GetCompetitorInsightsTool(BaseTool):
    """Get AI-analyzed insights about competitor patterns."""

    def __init__(self, config: Any):
        super().__init__(
            name="get_competitor_insights",
            description=(
                "Get analyzed insights about competitor channels: posting frequency, "
                "top-performing title patterns, thumbnail styles, trending topics, "
                "and engagement metrics. Specify a channel or get insights for all competitors."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel_url": {
                        "type": "string",
                        "description": "Specific channel URL (optional — all if not provided)",
                    },
                    "period_days": {
                        "type": "integer",
                        "description": "Number of days to analyze (default: 30)",
                    },
                },
            },
        )
        self.config = config

    def _get_videos(self, workspace: Path, channel_url: str | None, days: int) -> list[dict]:
        """Get videos from Supabase or local storage."""
        client = _get_supabase_client(self.config)
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        if client:
            try:
                query = (
                    client.table("yt_competitor_videos")
                    .select("*")
                    .gte("published_at", cutoff)
                    .order("published_at", desc=True)
                )
                if channel_url:
                    # Get competitor_id first
                    comp_result = client.table("yt_competitors").select("id").eq("channel_url", channel_url).execute()
                    if comp_result.data:
                        query = query.eq("competitor_id", comp_result.data[0]["id"])

                result = query.execute()
                return result.data
            except Exception as e:
                logger.error(f"Supabase error: {e}")

        # Fallback to local
        videos_file = workspace / "data" / "competitor_videos.json"
        if videos_file.exists():
            return json.loads(videos_file.read_text())

        return []

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        workspace = session.agent.config.workspace
        channel_url = args.get("channel_url")
        days = args.get("period_days", 30)

        videos = self._get_videos(workspace, channel_url, days)
        if not videos:
            return f"No video data found for the last {days} days. Run fetch_competitor_videos first to collect data."

        # Analyze patterns
        title_patterns = Counter(v.get("title_pattern", "other") for v in videos)
        thumbnail_styles = Counter(v.get("thumbnail_style", "unknown") for v in videos)

        # Top videos by views
        top_videos = sorted(videos, key=lambda v: v.get("view_count", 0), reverse=True)[:10]

        # Posting frequency
        publish_dates = []
        for v in videos:
            if v.get("published_at"):
                try:
                    if isinstance(v["published_at"], str):
                        dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
                    else:
                        dt = datetime.fromtimestamp(v["published_at"])
                    publish_dates.append(dt.date())
                except (ValueError, TypeError):
                    pass

        # Group by day of week
        day_counts = Counter()
        for d in publish_dates:
            day_counts[d.strftime("%A")] += 1

        # Average engagement by title pattern
        engagement_by_pattern = defaultdict(list)
        for v in videos:
            pattern = v.get("title_pattern", "other")
            eng = v.get("engagement_rate", 0)
            if eng:
                engagement_by_pattern[pattern].append(eng)

        avg_engagement = {}
        for pattern, engs in engagement_by_pattern.items():
            avg_engagement[pattern] = round(sum(engs) / len(engs), 2)

        # Most common tags
        all_tags = []
        for v in videos:
            all_tags.extend(v.get("tags", [])[:5])  # Top 5 tags per video
        top_tags = Counter(all_tags).most_common(15)

        # Build report
        lines = [f"## Competitor Insights (Last {days} Days)\n"]
        lines.append(f"**{len(videos)} videos analyzed**\n")

        lines.append("### 📈 Title Pattern Performance")
        for pattern, count in title_patterns.most_common():
            avg_eng = avg_engagement.get(pattern, 0)
            lines.append(f"- **{pattern}**: {count} videos | Avg engagement: {avg_eng}%")

        lines.append("\n### 🖼️ Thumbnail Styles")
        for style, count in thumbnail_styles.most_common():
            lines.append(f"- **{style}**: {count} videos")

        lines.append("\n### 📅 Posting Frequency (by day)")
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            count = day_counts.get(day, 0)
            if count:
                lines.append(f"- **{day}**: {count} uploads")

        lines.append("\n### 🔥 Top Performing Videos")
        for i, v in enumerate(top_videos[:5], 1):
            lines.append(f"{i}. **{v.get('title', 'Unknown')}**")
            lines.append(f"   Views: {v.get('view_count', 0):,} | Engagement: {v.get('engagement_rate', 0)}%")

        if top_tags:
            lines.append("\n### 🏷️ Trending Tags")
            for tag, count in top_tags[:10]:
                lines.append(f"- `{tag}` ({count} videos)")

        return "\n".join(lines)


class CompetitorReportTool(BaseTool):
    """Generate and send weekly competitor intelligence report."""

    def __init__(self, config: Any):
        super().__init__(
            name="competitor_report",
            description=(
                "Generate a weekly competitor intelligence report and send it to Telegram. "
                "Analyzes posting patterns, top content, title strategies, thumbnail styles, "
                "and provides actionable recommendations."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "period_days": {
                        "type": "integer",
                        "description": "Number of days for the report period (default: 7)",
                    },
                },
            },
        )
        self.config = config

    def _get_videos(self, workspace: Path, days: int) -> list[dict]:
        """Get recent videos."""
        client = _get_supabase_client(self.config)
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        if client:
            try:
                result = (
                    client.table("yt_competitor_videos")
                    .select("*")
                    .gte("published_at", cutoff)
                    .order("view_count", desc=True)
                    .execute()
                )
                return result.data
            except Exception:
                pass

        videos_file = workspace / "data" / "competitor_videos.json"
        if videos_file.exists():
            return json.loads(videos_file.read_text())
        return []

    def _get_competitors(self, workspace: Path) -> list[dict]:
        """Get list of competitors."""
        client = _get_supabase_client(self.config)
        if client:
            try:
                result = client.table("yt_competitors").select("*").eq("enabled", True).execute()
                return result.data
            except Exception:
                pass

        competitors_file = workspace / "data" / "competitors.json"
        if competitors_file.exists():
            return json.loads(competitors_file.read_text())
        return []

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        workspace = session.agent.config.workspace
        days = args.get("period_days", 7)

        videos = self._get_videos(workspace, days)
        competitors = self._get_competitors(workspace)

        if not videos:
            return "No competitor video data available. Run fetch_competitor_videos first, then try again."

        # Group by competitor
        by_competitor = defaultdict(list)
        for v in videos:
            comp_id = v.get("competitor_id", "unknown")
            by_competitor[comp_id].append(v)

        # Find top performers
        top_videos = sorted(videos, key=lambda v: v.get("view_count", 0), reverse=True)[:5]

        # Title pattern analysis
        title_patterns = Counter(v.get("title_pattern", "other") for v in videos)

        # Calculate weekly upload count per competitor
        upload_counts = {comp_id: len(vids) for comp_id, vids in by_competitor.items()}

        # Build Telegram-friendly report
        lines = [
            "🕵️ *Weekly Competitor Intelligence Report*",
            f"📅 Last {days} days | {len(videos)} videos from {len(competitors)} channels",
            "",
        ]

        lines.append("*🔥 Top Performing Videos:*")
        for i, v in enumerate(top_videos, 1):
            views = v.get("view_count", 0)
            title = v.get("title", "Unknown")[:80]
            lines.append(f"{i}. {title}")
            lines.append(f"   👁 {views:,} views")
        lines.append("")

        lines.append("*📊 Title Pattern Breakdown:*")
        for pattern, count in title_patterns.most_common():
            emoji = {
                "how_to": "📖",
                "list": "📋",
                "question": "❓",
                "comparison": "⚖️",
                "shocking": "😱",
                "tutorial": "🎓",
                "review": "⭐",
                "personal_experiment": "🧪",
            }.get(pattern, "📌")
            lines.append(f"{emoji} {pattern.replace('_', ' ').title()}: {count}")
        lines.append("")

        lines.append("*📈 Upload Activity:*")
        for comp_id, count in sorted(upload_counts.items(), key=lambda x: x[1], reverse=True):
            # Find competitor name
            name = comp_id
            for c in competitors:
                if c.get("id") == comp_id or c.get("channel_id") == comp_id:
                    name = c.get("channel_name", comp_id)
                    break
            lines.append(f"• {name}: {count} uploads this week")

        lines.append("")
        lines.append("*💡 Key Takeaway:*")
        most_common = title_patterns.most_common(1)
        if most_common:
            lines.append(
                f"The most successful title format this week was "
                f"*{most_common[0][0].replace('_', ' ')}* "
                f"({most_common[0][1]} videos)"
            )

        report_text = "\n".join(lines)

        # Send via telegram_notify if available
        try:
            from semeclaw.tools.experiment_tools import TelegramNotifyTool

            notify_tool = TelegramNotifyTool(workspace)
            await notify_tool.execute({"message": report_text}, session)
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

        # Save report
        period_end = datetime.now()
        period_start = period_end - timedelta(days=days)
        report_data = {
            "report_type": "weekly",
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "summary": report_text,
            "top_videos": [{"title": v.get("title"), "views": v.get("view_count")} for v in top_videos],
            "title_patterns": dict(title_patterns),
        }

        client = _get_supabase_client(self.config)
        if client:
            try:
                client.table("yt_competitor_reports").insert(report_data).execute()
            except Exception:
                pass

        # Save locally as fallback
        reports_dir = workspace / "data" / "competitor_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_file = reports_dir / f"report-{period_end.strftime('%Y-%m-%d')}.json"
        report_file.write_text(json.dumps(report_data, indent=2))

        return f"✓ Weekly competitor report generated ({len(videos)} videos analyzed)"


# ── Factory ────────────────────────────────────────────────────────────────────


def create_competitor_tools(config: Any) -> list[BaseTool]:
    """Create all competitor intelligence tools."""
    return [
        AddCompetitorTool(config),
        ListCompetitorsTool(config),
        FetchCompetitorVideosTool(config),
        GetCompetitorInsightsTool(config),
        CompetitorReportTool(config),
    ]
