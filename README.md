# Meta AI Bridge

A clean, public-ready FastAPI service for generating **images** and **videos** with Meta AI using a real browser session.

This project was built by reverse-engineering the browser flow used by `https://meta.ai/create`:
- `POST /api/graphql` using Server-Sent Events (SSE)
- collect `conversationId`
- fetch `GET /prompt/<conversationId>?_rsc=...`
- extract final image/video asset URLs

> Important: this repository does **not** contain any personal cookies, tokens, or session data.
> You must supply your own browser session cookies locally.

---

## Features

- `GET /healthz`
- `POST /image` → generate image URLs
- `POST /video` → generate video URLs (`.mp4` when available)
- `POST /download` → download one asset to local storage
- `POST /download/batch` → download many assets to local storage
- `POST /image/download` → generate images and save them locally in one call
- `POST /video/download` → generate videos and save them locally in one call
- `POST /image-to-video` → generate a video from an existing image/media entity
- `POST /image-to-video/download` → generate a video from an image and save it locally in one call
- Docker support
- clean `.env.example` for self-setup

---

## Project structure

```text
metaai-bridge-public/
├── app/
│   └── main.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── .gitignore
├── LICENSE
└── README.md
```

---

## Requirements

- Python 3.11+ (if running locally without Docker)
- Or Docker + Docker Compose
- A working browser session logged into `https://meta.ai`

---

## How authentication works

This project does **not** use an official Meta API key.
It uses the same authenticated browser session approach as the Meta AI web app.

### Required cookie inputs
At minimum, Meta AI currently needs a valid session containing cookies such as:
- `datr`
- `ecto_1_sess`

Other cookies/values often help stability:
- `wd`
- `dpr`
- `rd_challenge`

The easiest way is to provide a **single raw cookie string** in `.env`.

---

## How to get your own cookies safely

1. Open `https://meta.ai` in your browser and sign in.
2. Press `F12` to open DevTools.
3. Go to **Application** → **Cookies** → `https://meta.ai`
4. Copy the cookie values you need.
5. Create a local `.env` file from `.env.example`
6. Paste your values there.

### Recommended method
Use one raw cookie string:

```env
META_COOKIE_STRING=datr=...; ecto_1_sess=...; wd=1256x919; dpr=1; rd_challenge=...
```

### Important security note
- Never commit your `.env`
- Never share your real cookies publicly
- Rotate / refresh cookies if Meta AI stops responding

---

## Environment variables

Copy:

```bash
cp .env.example .env
```

Then edit `.env`.

### `.env.example`

```env
META_COOKIE_STRING=datr=...; ecto_1_sess=...; wd=1256x919; dpr=1; rd_challenge=...

META_AI_DATR=
META_AI_ECTO_1_SESS=
META_AI_WD=1256x919
META_AI_DPR=1
META_AI_RD_CHALLENGE=

TZ=Asia/Bangkok
META_DOWNLOAD_DIR=/app/downloads
META_USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36
META_ACCEPT_LANGUAGE=vi-VN,vi;q=0.9,fr-FR;q=0.8,fr;q=0.7,en-US;q=0.6,en;q=0.5
```

---

## Run with Docker

### 1. Build and start

```bash
docker compose up -d --build
```

### 2. Check health

```bash
curl http://127.0.0.1:18081/healthz
```

Expected:

```json
{
  "status": "ok",
  "cookies": "ready",
  "download_dir": "/app/downloads"
}
```

### 3. Downloads folder

Docker maps downloads here:

```text
./downloads -> /app/downloads
```

So files will appear locally inside:

```text
./downloads
```

---

## Run without Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then call:

```bash
curl http://127.0.0.1:8000/healthz
```

---

## API reference

## 1) Health

### `GET /healthz`

```bash
curl http://127.0.0.1:18081/healthz
```

---

## 2) Generate images

### `POST /image`

Meta image generation currently supports 3 orientations:
- `VERTICAL`
- `LANDSCAPE`
- `SQUARE`

#### Request

```json
{
  "prompt": "1 dog sleep in a yard",
  "orientation": "VERTICAL",
  "timeout_seconds": 90
}
```

#### Example

```bash
curl -X POST http://127.0.0.1:18081/image \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt":"1 dog sleep in a yard",
    "orientation":"VERTICAL",
    "timeout_seconds":90
  }'
```

#### Response

```json
{
  "success": true,
  "conversation_id": "...",
  "image_urls": [
    "https://scontent-...jpeg",
    "https://scontent-...jpeg"
  ],
  "complete_seen": true,
  "event_count": 12
}
```

---

## 3) Generate videos

### `POST /video`

Video generation is slower than image generation.
This service will poll prompt state until `.mp4` links appear or polling is exhausted.

#### Request

```json
{
  "prompt": "blonde lady in the sun",
  "timeout_seconds": 180,
  "poll_attempts": 12,
  "poll_interval_seconds": 5
}
```

#### Example

```bash
curl -X POST http://127.0.0.1:18081/video \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt":"blonde lady in the sun",
    "timeout_seconds":180,
    "poll_attempts":12,
    "poll_interval_seconds":5
  }'
```

#### Response

```json
{
  "success": true,
  "conversation_id": "...",
  "video_urls": [
    "https://scontent-...mp4"
  ],
  "complete_seen": true,
  "event_count": 15,
  "poll_attempts_used": 12,
  "note": "Video flow auto-polls prompt state until mp4 links appear or attempts are exhausted."
}
```

---

## Upload support

### `POST /upload`

This endpoint uploads an image to Meta AI and returns a `source_media_ent_id` that can be used with `POST /image-to-video`.

Important:
- upload flow may require a valid `META_AI_ACCESS_TOKEN` (`ecto1:...`) in addition to browser cookies
- this token can expire and may need refreshing

Example using curl:

```bash
curl -X POST http://127.0.0.1:18081/upload \
  -F "file=@./example.jpg"
```

Typical response:

```json
{
  "success": true,
  "source_media_ent_id": "123456789012345",
  "upload_session_id": "...",
  "file_name": "example.jpg",
  "file_size": 100203,
  "mime_type": "image/jpeg"
}
```

## 4) Download one asset

### `POST /download`

#### Request

```json
{
  "url": "https://example.com/file.jpg",
  "filename": "optional-name.jpg",
  "subdir": "tests"
}
```

#### Example

```bash
curl -X POST http://127.0.0.1:18081/download \
  -H 'Content-Type: application/json' \
  -d '{
    "url":"https://example.com/file.jpg",
    "subdir":"tests"
  }'
```

---

## 5) Download many assets

### `POST /download/batch`

```bash
curl -X POST http://127.0.0.1:18081/download/batch \
  -H 'Content-Type: application/json' \
  -d '{
    "urls":[
      "https://example.com/a.jpg",
      "https://example.com/b.jpg"
    ],
    "subdir":"batch-1",
    "prefix":"image"
  }'
```

---

## 6) Generate images and download in one step

### `POST /image/download`

```bash
curl -X POST http://127.0.0.1:18081/image/download \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt":"cyberpunk cat",
    "orientation":"VERTICAL",
    "timeout_seconds":90,
    "subdir":"image-runs",
    "filename_prefix":"cat"
  }'
```

This returns both:
- generation metadata
- local download results

---

## 7) Generate videos and download in one step

### `POST /video/download`

```bash
curl -X POST http://127.0.0.1:18081/video/download \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt":"blonde lady in the sun",
    "timeout_seconds":180,
    "poll_attempts":12,
    "poll_interval_seconds":5,
    "subdir":"video-runs",
    "filename_prefix":"clip"
  }'
```

---

## 8) Generate video from an image/media item

### `POST /image-to-video`

```bash
curl -X POST http://127.0.0.1:18081/image-to-video \
  -H 'Content-Type: application/json' \
  -d '{
    "source_media_ent_id":"913199711508625",
    "source_media_url":"blob:https://meta.ai/ed8e0c9f-d9b3-4026-a911-7d4e7dd3a67f",
    "prompt":"play guitar",
    "timeout_seconds":180,
    "poll_attempts":12,
    "poll_interval_seconds":5
  }'
```

Notes:
- `source_media_ent_id` is the key field.
- `source_media_url` can help, but Meta may still resolve via the entity id.
- `conversation_id`, `entry_point`, `current_branch_path`, and `is_new_conversation` can be passed when reproducing a specific browser flow.

### `POST /image-to-video/download`

```bash
curl -X POST http://127.0.0.1:18081/image-to-video/download \
  -H 'Content-Type: application/json' \
  -d '{
    "source_media_ent_id":"913199711508625",
    "source_media_url":"blob:https://meta.ai/ed8e0c9f-d9b3-4026-a911-7d4e7dd3a67f",
    "prompt":"play guitar",
    "timeout_seconds":180,
    "poll_attempts":12,
    "poll_interval_seconds":5,
    "subdir":"image-to-video-runs",
    "filename_prefix":"clip"
  }'
```

---

## Troubleshooting

### `cookies: incomplete`
Your `.env` is missing required session values.
Check:
- `META_COOKIE_STRING`
- or `META_AI_DATR`
- and `META_AI_ECTO_1_SESS`

### Images work but videos do not
Try:
- increasing `timeout_seconds`
- increasing `poll_attempts`
- increasing `poll_interval_seconds`
- refreshing your Meta AI browser cookies

Suggested video-safe values:

```json
{
  "timeout_seconds": 180,
  "poll_attempts": 12,
  "poll_interval_seconds": 5
}
```

### Meta changes its frontend again
This project depends on the current web flow used by Meta AI.
If Meta changes request structure, headers, or render flow, the code may need to be updated.

---

## Privacy and safety

This public repository intentionally excludes:
- real cookies
- real session values
- HAR files containing personal browsing data
- downloaded generated assets from private sessions

You are responsible for protecting your own session cookies.

---

## License

MIT
