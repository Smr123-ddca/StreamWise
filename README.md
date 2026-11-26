# StreamWise

StreamWise is an intelligent video streaming optimization system designed to deliver high-quality video playback while dynamically conserving bandwidth. Rather than relying solely on conventional throughput-based Adaptive Bitrate (ABR) streaming, StreamWise analyzes visual content complexity, incorporates viewer preferences, and monitors real-time network conditions to allocate video quality intelligently at the segment level.

---

## Intended Functionality

- **Content-Aware Bitrate Allocation**: Analyze video segments for spatial and temporal complexity (e.g., distinguishing static talking heads from high-motion action scenes) to allocate bitrates where visual fidelity matters most.
- **Preference-Driven Streaming**: Allow viewers to specify streaming priorities, including Data Saver, Balanced, and Quality Priority modes.
- **Network-Aware Delivery**: Ingest real-time client telemetry to predict throughput and buffer constraints, mitigating rebuffering events before they occur.
- **Dynamic Segment-Level Quality Selection**: Serve optimized HLS segments tailored to current viewing conditions and content demands.

---

## Technology Stack

### Backend
- **Language**: Python 3.11+
- **Framework**: [FastAPI](https://fastapi.tiangolo.com/)
- **Server**: [Uvicorn](https://www.uvicorn.org/)
- **Configuration & Validation**: [Pydantic](https://docs.pydantic.dev/) & [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

### Video Processing & Analysis
- **FFmpeg & FFprobe**: File inspection, metadata extraction, transcoding ladders, and HLS packaging.
- **PySceneDetect**: Scene boundary and transition detection (future module).
- **OpenCV**: Spatial and temporal visual complexity scoring (future module).

### Frontend (Future Modules)
- **Framework**: React
- **Build Tool**: Vite
- **Styling**: Tailwind CSS
- **Player**: Shaka Player (HLS streaming and custom ABR hooks)

### Database (Future Integration)
- **Database Engine**: PostgreSQL

---

## System Requirements: FFmpeg & FFprobe

StreamWise relies on `ffprobe` for media validation and technical metadata extraction.

### Installation Instructions

- **Windows (via WinGet)**:
  ```powershell
  winget install --id Gyan.FFmpeg.Essentials -e --source winget
  ```
- **macOS (via Homebrew)**:
  ```bash
  brew install ffmpeg
  ```
- **Linux (Ubuntu / Debian)**:
  ```bash
  sudo apt update && sudo apt install -y ffmpeg
  ```

If `ffprobe` is installed in a custom location not present on the system `PATH`, configure its path in `.env`:
```dotenv
FFPROBE_PATH="C:\ffmpeg\bin\ffprobe.exe"
```

---

## Project Structure

```text
StreamWise/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI application & router mounting
│   │   ├── api/
│   │   │   ├── __init__.py          # API route exports
│   │   │   └── video_routes.py      # Video upload & management endpoints
│   │   ├── modules/
│   │   │   ├── video_processing/    # Upload, validation, metadata, transcoding, & HLS packaging
│   │   │   │   ├── __init__.py
│   │   │   │   ├── hls_config.py          # HLS segment duration, GOP & RFC 6381 codec helpers
│   │   │   │   ├── hls_packager.py        # Synchronized FFmpeg HLS packager & atomic promoter
│   │   │   │   ├── hls_schemas.py         # HLS playlist, segment & status Pydantic schemas
│   │   │   │   ├── metadata.py            # FFprobe execution & JSON parsing
│   │   │   │   ├── schemas.py             # Pydantic schemas (VideoMetadata, VideoUploadResponse)
│   │   │   │   ├── service.py             # Upload, transcoding & HLS pipeline coordinator
│   │   │   │   ├── transcoder.py          # FFmpeg multi-resolution transcoding engine
│   │   │   │   ├── transcoding_config.py  # Centralized encoding & resolution profile ladder
│   │   │   │   ├── transcoding_schemas.py # Transcoding response & rendition schemas
│   │   │   │   ├── uploader.py            # Chunked streaming & size enforcement
│   │   │   │   └── validator.py           # Format & extension validation
│   │   │   ├── content_analysis/    # Scene detection & complexity (planned)
│   │   │   ├── preferences/         # User streaming preferences (planned)
│   │   │   ├── network_monitor/     # Telemetry & bandwidth estimation (planned)
│   │   │   └── decision_engine/     # Segment-level quality allocator (planned)
│   │   ├── database/                # Database connection & models (planned)
│   │   ├── storage/                 # Scoped HLS media delivery, range & playlist security
│   │   │   ├── __init__.py
│   │   │   └── media_storage.py     # MediaDeliveryStorage resolver & byte-range streamer
│   │   └── config/                  # Application configuration & settings
│   ├── tests/                       # Backend test suite (unittest / pytest)
│   │   ├── test_end_to_end_pipeline.py # Full upload-to-transcode E2E integration tests
│   │   ├── test_health.py           # Health check endpoint tests
│   │   ├── test_hls_delivery.py     # HLS HTTP delivery, security, Range & CORS tests
│   │   ├── test_hls_packager.py     # Synchronized HLS packaging & alignment tests
│   │   ├── test_transcoder.py       # Multi-resolution transcoding engine tests
│   │   └── test_video_upload.py     # Video upload, validation & metadata test suite
│   ├── requirements.txt             # Initial Python dependencies
│   └── .env.example                 # Environment variable templates
├── frontend/                        # Playback verification UI & future React workspace
│   ├── player.html                  # Minimal HLS playback test page (hls.js CDN + native fallback)
│   └── README.md
├── storage/                         # Local media storage directory
│   ├── uploads/                     # Raw incoming video files (organized by UUID)
│   └── processed/                   # Transcoded and segmented streams
├── docs/
│   └── architecture.md              # Detailed architecture & module specs
├── .gitignore
└── README.md
```

---

## Backend Setup & Local Development

### 1. Prerequisites
- Python 3.11 or higher
- FFmpeg and FFprobe installed and available on system PATH
- `pip` package manager

### 2. Create and Activate a Virtual Environment

From the root of the repository:

**On Linux/macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**On Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies

Navigate to the `backend/` directory and install the requirements:

```bash
cd backend
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create your local `.env` configuration file from the template:

```bash
cp .env.example .env
```

On Windows PowerShell:
```powershell
Copy-Item .env.example .env
```

### 5. Run the FastAPI Development Server

Start the development server using Uvicorn:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Alternatively, from the repository root:
```bash
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

---

## Video Upload API

### Supported Formats
- **MP4** (`.mp4`)
- **MOV** (`.mov`)
- **MKV** (`.mkv`)
- **WebM** (`.webm`)

### Endpoint
`POST /api/videos/upload` (Content-Type: `multipart/form-data`)

### Request Example

Using `curl`:
```bash
curl -X POST "http://127.0.0.1:8000/api/videos/upload" \
  -F "file=@sample.mp4;type=video/mp4"
```

### Successful Response (HTTP 200 OK)

```json
{
  "video_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "original_filename": "sample.mp4",
  "status": "uploaded",
  "upload_size": 18993,
  "metadata": {
    "duration": 1.0,
    "width": 320,
    "height": 240,
    "resolution": "320x240",
    "frame_rate": "30/1",
    "video_codec": "h264",
    "audio_codec": "aac",
    "container": "mov,mp4,m4a,3gp,3g2,mj2",
    "bitrate": 151944,
    "file_size": 18993
  },
  "created_at": "2026-11-16T07:15:00.000000+00:00"
}
```

### Storage Layout
Uploaded files are stored safely in an isolated folder named by their `video_id`:
```text
storage/uploads/{video_id}/source.{extension}
```

### Error Responses
- **400 Bad Request**: Missing filename or empty file (0 bytes).
- **413 Content Too Large**: File exceeds the configured `MAX_UPLOAD_SIZE_MB`.
- **415 Unsupported Media Type**: Unsupported extension or non-video MIME type.
- **422 Unprocessable Content**: Invalid or corrupted video, or missing video stream.
- **503 Service Unavailable**: FFprobe is not installed or unreachable on the host.

---

## Multi-Resolution Video Transcoding API

StreamWise transcodes uploaded videos into standardized quality renditions using FFmpeg (H.264 / AAC) with aspect ratio preservation, mod-2 dimension enforcement, and strict upscaling prevention.

### Supported Renditions & Encoding Configuration

Centralized in `backend/app/modules/video_processing/transcoding_config.py`:

| Rendition | Max Resolution | Target Height | Video Codec | Preset | CRF | Pixel Format | Audio Codec | Audio Bitrate | Moov Optimization |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **360p** | 640x360 | 360 | H.264 (`libx264`) | `fast` | 26 | `yuv420p` | AAC (`aac`) | 96 kbps | `+faststart` |
| **480p** | 854x480 | 480 | H.264 (`libx264`) | `fast` | 24 | `yuv420p` | AAC (`aac`) | 128 kbps | `+faststart` |
| **720p** | 1280x720 | 720 | H.264 (`libx264`) | `fast` | 22 | `yuv420p` | AAC (`aac`) | 128 kbps | `+faststart` |

- **Preservation of Aspect Ratio**: Scaled dimensions maintain original source proportions across landscape (16:9, 4:3, 21:9 ultrawide), portrait (9:16), and square (1:1) media.
- **Even Dimension Policy**: Output dimensions are guaranteed to be even numbers (`mod 2`) to ensure compatibility with H.264 macroblocks and YUV420p chroma subsampling.
- **No Unnecessary Upscaling**: Renditions requiring scaling beyond the source dimensions are skipped and the omission reason is recorded in the response.
- **Silent Video Support**: When the source contains no audio stream, transcoding proceeds without attempting audio mapping (`-an`), generating valid audio-free renditions.
- **Source Immutability**: The original uploaded file in `storage/uploads/` is never modified or overwritten.

### Processed Output Structure

Transcoded videos are stored separately under `storage/processed/`:

```text
storage/processed/{video_id}/
├── 360p/
│   └── video.mp4
├── 480p/
│   └── video.mp4
├── 720p/
│   └── video.mp4
└── transcode_status.json
```

### Endpoints

#### 1. Trigger Transcoding
`POST /api/videos/{video_id}/transcode` (optional query param: `?force=true`)

Executes the transcoding pipeline for the specified UUID `video_id`. If already completed and outputs exist on disk, returns the existing result idempotently unless `force=true` is specified. Concurrent duplicate requests for an in-progress `video_id` return `409 Conflict`.

**Example Request:**
```bash
curl -X POST "http://127.0.0.1:8000/api/videos/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/transcode"
```

**Example Response (200 OK):**
```json
{
  "video_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "status": "completed",
  "overall_status": "completed",
  "successful_renditions": [
    {
      "rendition": "360p",
      "width": 640,
      "height": 360,
      "resolution": "640x360",
      "file_path": "storage/processed/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/360p/video.mp4",
      "file_size": 184512,
      "duration": 1.0,
      "video_codec": "h264",
      "audio_codec": "aac",
      "bitrate": 1476096
    },
    {
      "rendition": "480p",
      "width": 854,
      "height": 480,
      "resolution": "854x480",
      "file_path": "storage/processed/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/480p/video.mp4",
      "file_size": 312480,
      "duration": 1.0,
      "video_codec": "h264",
      "audio_codec": "aac",
      "bitrate": 2499840
    },
    {
      "rendition": "720p",
      "width": 1280,
      "height": 720,
      "resolution": "1280x720",
      "file_path": "storage/processed/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/720p/video.mp4",
      "file_size": 524108,
      "duration": 1.0,
      "video_codec": "h264",
      "audio_codec": "aac",
      "bitrate": 4192864
    }
  ],
  "skipped_renditions": [],
  "failed_renditions": [],
  "processing_duration": 1.842
}
```

#### 2. Get Transcoding Status
`GET /api/videos/{video_id}/transcode/status`

Retrieves the current transcoding state and rendition details for a video ID.

**Example Request:**
```bash
curl "http://127.0.0.1:8000/api/videos/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/transcode/status"
```

### Error Responses
- **400 Bad Request**: Invalid `video_id` format (must be a valid UUID).
- **404 Not Found**: Source video does not exist or transcoding has not been initiated for this video ID.
- **409 Conflict**: Transcoding or HLS packaging is already in progress for this video ID.
- **422 Unprocessable Content**: Corrupted source file or media parsing failure.
- **503 Service Unavailable**: FFmpeg or FFprobe binaries are missing or unreachable on the system PATH.

---

## HLS Packaging API

StreamWise packages existing transcoded MP4 renditions into synchronized **MPEG-TS HLS** streams (`4`-second closed-GOP segments, rendition `playlist.m3u8`, and adaptive `master.m3u8`), while leaving original uploads and MP4 renditions untouched.

### HLS Output Structure

```text
storage/processed/{video_id}/hls/
├── master.m3u8
├── hls_status.json
├── 360p/
│   ├── playlist.m3u8
│   ├── segment_000.ts
│   └── segment_001.ts
├── 480p/
│   ├── playlist.m3u8
│   └── ...
└── 720p/
    ├── playlist.m3u8
    └── ...
```

### Endpoints

#### 1. Package Video to HLS
`POST /api/videos/{video_id}/hls` (optional query param: `?force=true`)

- Packages all valid, available MP4 renditions for `{video_id}` with fixed closed-GOP keyframes aligned across resolutions (including fractional frame rates such as `23.976` and `29.97` fps).
- Validates every `.ts` segment with FFprobe to confirm IDR keyframe start, dimensions, codecs, and cross-rendition PTS alignment before atomic promotion.
- Returns existing completed HLS metadata idempotently unless `?force=true` is specified.
- Concurrent duplicate requests return `409 Conflict`. If a forced re-packaging fails completely, any previously valid `hls/` directory is preserved.

**Example Request:**
```bash
curl -X POST "http://127.0.0.1:8000/api/videos/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/hls"
```

#### 2. Get HLS Packaging Status
`GET /api/videos/{video_id}/hls/status`

```bash
curl "http://127.0.0.1:8000/api/videos/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/hls/status"
```

### Verifying Generated Playlists & Segments Locally

Inspect `master.m3u8`, rendition `playlist.m3u8`, and confirm segment keyframe alignment with `ffprobe`:
```powershell
Get-Content storage\processed\<video_id>\hls\master.m3u8
Get-Content storage\processed\<video_id>\hls\360p\playlist.m3u8
ffprobe -v error -select_streams v:0 -read_intervals "%+#3" -show_entries frame=pts_time,pict_type,key_frame -of json storage\processed\<video_id>\hls\360p\segment_000.ts
```

### Known Limitations
- **Synchronous Execution**: `POST /api/videos/{video_id}/transcode` and `POST /api/videos/{video_id}/hls` run synchronously; long multi-minute videos may require raising `TRANSCODING_TIMEOUT_SECONDS` / `HLS_TIMEOUT_SECONDS` until background job queues are added.
- **No Database Persistence**: Job statuses are persisted in per-video JSON files on disk (`transcode_status.json`, `hls_status.json`) without PostgreSQL yet.
- **External CDN Dependency for Test Player**: `frontend/player.html` loads `hls.js` (`v1.5.15`) from jsDelivr CDN when MSE playback is used.

---

## HLS Video Delivery & Playback Verification

StreamWise serves validated HLS playlists and MPEG-TS segments via scoped HTTP endpoints (without exposing unrestricted `StaticFiles` access or raw host filesystem paths).

### Delivery Endpoints (`GET` & `HEAD`)

| Endpoint | MIME Type | Cache-Control | Purpose |
| :--- | :--- | :--- | :--- |
| `/api/videos/{video_id}/hls/master.m3u8` | `application/vnd.apple.mpegurl` | `no-cache, must-revalidate` | Adaptive master playlist |
| `/api/videos/{video_id}/hls/{rendition}/playlist.m3u8` | `application/vnd.apple.mpegurl` | `no-cache, must-revalidate` | Rendition media playlist |
| `/api/videos/{video_id}/hls/{rendition}/{segment_name}` | `video/mp2t` | `public, max-age=3600` | MPEG-TS segment (`Range` `206`/`416` supported) |
| `/player` | `text/html` | — | Minimal HLS playback verification page |

### Security & HTTP Behavior
- **Strict Containment**: Only files inside `storage/processed/{video_id}/hls/` are accessible; symlinks (`403`), traversal (`400`), non-`.ts` segment requests (`400`), and raw uploads/MP4s/status JSONs are blocked.
- **Playlist Validation**: Master and rendition playlists are parsed and validated before serving; corrupted files or unsafe URIs (absolute paths, schemes, `..`) return `422`.
- **Byte-Range & Conditional Headers**: Segments and playlists expose `Accept-Ranges: bytes`, `ETag`, `Last-Modified`, `Content-Length`, and `X-Content-Type-Options: nosniff`, supporting `206 Partial Content` and `416 Range Not Satisfiable`.

### End-to-End Local Playback Test Steps

1. **Start the FastAPI backend** from the repository root:
   ```powershell
   $env:PYTHONPATH="."; python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
   ```
2. **Upload, transcode, and package a video**:
   ```powershell
   curl.exe -X POST "http://127.0.0.1:8000/api/videos/upload" -F "file=@sample.mp4;type=video/mp4"
   curl.exe -X POST "http://127.0.0.1:8000/api/videos/<video_id>/transcode"
   curl.exe -X POST "http://127.0.0.1:8000/api/videos/<video_id>/hls"
   ```
3. **Open the playback verification page** in a browser:
   ```text
   http://127.0.0.1:8000/player?video_id=<video_id>
   ```
   - Verify `hls.js` loads (or native HLS in Safari), plays from start to finish, updates current resolution/buffer telemetry, and switches renditions via the **Quality** dropdown (`Auto`, `360p`, `480p`, `720p`).
   - Alternatively, verify HTTP stream demuxing via FFmpeg:
     ```powershell
     ffmpeg -v error -i "http://127.0.0.1:8000/api/videos/<video_id>/hls/master.m3u8" -map 0:p:0 -f null -
     ```

---

## Health Diagnostic Endpoint

```bash
curl http://127.0.0.1:8000/api/health
```

### Response
```json
{
  "application": "StreamWise",
  "status": "healthy",
  "version": "0.1.0"
}
```

Interactive OpenAPI documentation:
- **Swagger UI**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

---

## Running Automated Tests

Run the automated test suite using `unittest`:
```bash
python -m unittest discover -s backend/tests -v
```

Or using `pytest`:
```bash
pytest backend/tests -v
```

To run the HLS delivery, packaging, transcoding, and end-to-end integration suites:
```bash
pytest backend/tests/test_hls_delivery.py backend/tests/test_hls_packager.py backend/tests/test_transcoder.py backend/tests/test_end_to_end_pipeline.py -v
```
