# Ad Audio Assets

Place 20–30 second MP3 ad files here for the War Room loading screen.

## Files

- `nervix-default.mp3` — Default NERVIX brand ad played when no campaign is active.

## Generating a placeholder

```bash
ffmpeg -f lavfi -i "sine=frequency=1000:duration=25" -pix_fmt yuv420p data/ads/nervix-default.mp3
```

Replace with a real brand voiceover before production.
