# Cloudflare Infrastructure for SemeClaw

## Overview

Three Cloudflare services power the SemeClaw update loop and asset delivery:

| Service | Domain / URL | Purpose |
|---------|-------------|---------|
| **Worker** | `https://semeclaw-updates.semebitcoin.workers.dev` | Proxies `manifest.json` and ad MP3s from R2 with CORS headers |
| **R2** | `semeclaw-assets` bucket | Hosts manifest.json, ad MP3s, demo videos, banner images |
| **Pages** | `updates.semeclaw.com` (optional) | Static fallback for the update landing page |

## Architecture

```
Client (SemeClaw)
    ↓ GET /manifest.json
Cloudflare Worker (semeclaw-updates)
    ↓ AWS Signature V4
Cloudflare R2 (semeclaw-assets bucket)
    → manifest.json, ads/nervix-default.mp3, etc.
```

The Worker signs requests to R2 using S3-compatible API credentials stored as **secret bindings**.

## Prerequisites

1. Cloudflare account
2. GitHub repository secrets:
   - `CLOUDFLARE_API_TOKEN` (Edit Cloudflare Workers + R2 permissions)
   - `CLOUDFLARE_ACCOUNT_ID`
   - `R2_ENDPOINT_URL`
   - `R2_ACCESS_KEY_ID`
   - `R2_SECRET_ACCESS_KEY`

## 1. Cloudflare Worker — Update Server

### Source code

`cloudflare/worker/index.js` — the Worker script that proxies R2 content.

### Deploy via Dashboard

1. Go to **Workers & Pages** → **Create** → **Create Worker**
2. Name it `semeclaw-updates`
3. Replace the default code with `cloudflare/worker/index.js`
4. **Settings** → **Variables and Secrets** → Add these secrets:
   - `R2_ACCESS_KEY_ID`
   - `R2_SECRET_ACCESS_KEY`
   - `R2_ENDPOINT_URL` (e.g. `https://<account-id>.r2.cloudflarestorage.com`)
   - `R2_BUCKET_NAME` (e.g. `semeclaw-assets`)
5. **Settings** → **Domains & Routes** → Enable `workers.dev` subdomain
6. **Save and Deploy**

### Test endpoints

```bash
curl https://semeclaw-updates.semebitcoin.workers.dev/health
curl https://semeclaw-updates.semebitcoin.workers.dev/manifest.json
curl -o /tmp/ad.mp3 https://semeclaw-updates.semebitcoin.workers.dev/ads/nervix-default.mp3
```

## 2. Cloudflare R2 — Asset Bucket

### Create the bucket

```bash
npx wrangler r2 bucket create semeclaw-assets
```

### Upload assets

```bash
pip install boto3
python cloudflare/r2/upload-assets.py
```

Or use the Cloudflare dashboard to upload manually.

### Required bucket structure

```
semeclaw-assets/
├── manifest.json
├── ads/
│   └── nervix-default.mp3
├── advertisement/
│   ├── nervix_ad.mp3
│   ├── nervix_ad.txt
│   └── nervix_banner.png
├── demo/
│   ├── meeting_background.wav
│   ├── nervix_jingle.wav
│   └── semeclaw_demo.mp4
└── screenshots/
    └── ...
```

## 3. Manifest Format

`manifest.json` in R2 follows this schema:

```json
{
  "schema_version": "1.0",
  "updates": [
    {
      "version": "0.8.20260421",
      "docker_image": "ghcr.io/dansidanutz/semeclaw:0.8.20260421",
      "ad_url": "https://semeclaw-assets.1fc06dad3c7f42576be40fc6437f8fec.r2.cloudflarestorage.com/ads/nervix-default.mp3",
      "project_id": "nervix-default",
      "release_notes": "..."
    }
  ],
  "latest_version": "0.8.20260421",
  "latest_ad_url": "...",
  "latest_project_id": "nervix-default",
  "manifest_server": "https://semeclaw-updates.1fc06dad3c7f42576be40fc6437f8fec.workers.dev"
}
```

SemeClaw clients poll the Worker and compare `latest_version` against their local install.

## 4. CI/CD Integration

The `.github/workflows/deploy-updates.yml` workflow runs on pushes to `main` that touch:
- `cloudflare/worker/*`
- `cloudflare/pages/*`
- `assets/*`

It uploads changed assets to R2 and deploys the Worker.
