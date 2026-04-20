# Cloudflare Infrastructure for SemeClaw

## Overview

Two Cloudflare services power the SemeClaw update loop and asset delivery:

| Service | Domain | Purpose |
|---------|--------|---------|
| **Pages** | `updates.semeclaw.com` | Hosts `manifest.json` — the update manifest that SemeClaw clients poll for new versions |
| **R2** | `assets.semeclaw.com` | Hosts ad MP3s, demo videos, banner images, and other static assets |

## Prerequisites

1. Cloudflare account
2. Domain `semeclaw.com` added to Cloudflare
3. GitHub repository secrets:
   - `CLOUDFLARE_API_TOKEN` (Edit Cloudflare Workers + Pages + R2 permissions)
   - `CLOUDFLARE_ACCOUNT_ID`
   - `R2_ENDPOINT_URL`
   - `R2_ACCESS_KEY_ID`
   - `R2_SECRET_ACCESS_KEY`

## 1. Cloudflare Pages — Update Manifest

### Create the Pages project

```bash
npx wrangler pages project create updates-semeclaw
```

Or use the Cloudflare dashboard:
- **Pages** → **Create a project** → **Upload assets**
- Project name: `updates-semeclaw`
- Production branch: `main`

### Add the custom domain

Dashboard → `updates-semeclaw` → **Custom domains** → `updates.semeclaw.com`

### Deploy manually (first time)

```bash
cd cloudflare/pages
npx wrangler pages deploy . --project-name=updates-semeclaw --branch=main
```

### Automated deploy via GitHub Actions

The `.github/workflows/deploy-updates.yml` workflow runs on every push to `main` that touches `cloudflare/pages/` or `assets/`.

## 2. Cloudflare R2 — Asset Bucket

### Create the bucket

```bash
npx wrangler r2 bucket create semeclaw-assets
```

### Enable public access (custom domain)

Dashboard → **R2** → `semeclaw-assets` → **Settings** → **Public access** → `assets.semeclaw.com`

### Upload assets

```bash
# Install boto3
pip install boto3

# Set credentials from Cloudflare R2 dashboard
export AWS_ACCESS_KEY_ID=your-r2-access-key
export AWS_SECRET_ACCESS_KEY=your-r2-secret-key
export R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com

# Upload
python cloudflare/r2/upload-assets.py
```

### Automated upload via GitHub Actions

The `deploy-updates.yml` workflow also uploads any changed assets in `assets/` to R2.

## 3. Daily Release Pipeline

The `.github/workflows/daily-release.yml` workflow:

1. Runs daily at 06:00 UTC (or manually)
2. Bumps the patch version in `pyproject.toml`
3. Generates a new manifest with the updated version
4. Commits and pushes a tag
5. Deploys the manifest to Pages
6. Triggers Docker build and push to GHCR
7. Creates a GitHub Release

### Run manually

Go to **Actions** → **Daily Release Pipeline** → **Run workflow**.

## Manifest Format

`updates.semeclaw.com/manifest.json` follows this schema:

```json
{
  "id": "semeclaw",
  "version": "0.7.0",
  "channel": "stable",
  "released_at": "2026-04-20T23:00:00Z",
  "download_url": "https://github.com/DansiDanutz/SemeClaw/archive/refs/tags/v0.7.0.zip",
  "docker_image": "ghcr.io/dansidanutz/semeclaw:0.7.0",
  "changelog_url": "https://github.com/DansiDanutz/SemeClaw/blob/main/CHANGELOG.md",
  "assets": {
    "ad_mp3": "https://assets.semeclaw.com/advertisement/nervix_ad.mp3",
    "banner": "https://assets.semeclaw.com/advertisement/nervix_banner.png",
    "demo_video": "https://assets.semeclaw.com/demo/semeclaw_demo.mp4"
  },
  "minimum_version": "0.6.0",
  "force_update": false
}
```

SemeClaw clients poll this manifest and compare `version` against their local install.
