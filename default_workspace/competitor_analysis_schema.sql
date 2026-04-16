-- Competitor Analysis Module
-- Run this SQL in your Supabase project to set up competitor intelligence tables.

-- Competitor channels table
CREATE TABLE IF NOT EXISTS yt_competitors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_url TEXT NOT NULL UNIQUE,
    channel_id TEXT UNIQUE,
    channel_name TEXT,
    channel_description TEXT,
    subscriber_count INTEGER,
    total_views BIGINT,
    video_count INTEGER,
    niche TEXT,
    country TEXT,
    custom_tags TEXT[],
    notes TEXT,
    enabled BOOLEAN DEFAULT true,
    last_fetched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Competitor videos table
CREATE TABLE IF NOT EXISTS yt_competitor_videos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    competitor_id UUID REFERENCES yt_competitors(id) ON DELETE CASCADE,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    published_at TIMESTAMPTZ,
    view_count INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    duration_seconds INTEGER,
    tags TEXT[],
    thumbnail_url TEXT,
    thumbnail_style TEXT,  -- e.g., "face_reaction", "text_overlay", "before_after", "listicle"
    title_pattern TEXT,     -- e.g., "question", "list", "shocking", "how_to", "comparison"
    engagement_rate NUMERIC(5,2),  -- (likes + comments) / views * 100
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(competitor_id, video_id)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_competitor_videos_competitor ON yt_competitor_videos(competitor_id);
CREATE INDEX IF NOT EXISTS idx_competitor_videos_published ON yt_competitor_videos(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_competitor_videos_views ON yt_competitor_videos(view_count DESC);
CREATE INDEX IF NOT EXISTS idx_competitor_videos_pattern ON yt_competitor_videos(title_pattern);
CREATE INDEX IF NOT EXISTS idx_competitor_videos_thumbnail ON yt_competitor_videos(thumbnail_style);

-- Competitor analysis reports table
CREATE TABLE IF NOT EXISTS yt_competitor_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_type TEXT NOT NULL DEFAULT 'weekly',  -- 'daily', 'weekly', 'monthly'
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    summary TEXT,
    top_performing_videos JSONB,
    title_patterns JSONB,
    posting_frequency JSONB,
    trending_topics JSONB,
    thumbnail_insights JSONB,
    recommendations TEXT[],
    telegram_sent BOOLEAN DEFAULT false,
    telegram_message_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Row Level Security (RLS) - adjust as needed
ALTER TABLE yt_competitors ENABLE ROW LEVEL SECURITY;
ALTER TABLE yt_competitor_videos ENABLE ROW LEVEL SECURITY;
ALTER TABLE yt_competitor_reports ENABLE ROW LEVEL SECURITY;

-- Allow all operations (adjust for production)
CREATE POLICY "Allow all on yt_competitors" ON yt_competitors FOR ALL USING (true);
CREATE POLICY "Allow all on yt_competitor_videos" ON yt_competitor_videos FOR ALL USING (true);
CREATE POLICY "Allow all on yt_competitor_reports" ON yt_competitor_reports FOR ALL USING (true);
