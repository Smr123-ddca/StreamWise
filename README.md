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
│   │   │   ├── video_processing/    # Upload streaming, validation, FFprobe metadata
│   │   │   │   ├── __init__.py
│   │   │   │   ├── metadata.py      # FFprobe execution & JSON parsing
│   │   │   │   ├── schemas.py       # Pydantic schemas (VideoMetadata, VideoUploadResponse)
│   │   │   │   ├── service.py       # Upload & extraction pipeline coordinator
│   │   │   │   ├── uploader.py      # Chunked streaming & size enforcement
│   │   │   │   └── validator.py     # Format & extension validation
│   │   │   ├── content_analysis/    # Scene detection & complexity (planned)
│   │   │   ├── preferences/         # User streaming preferences (planned)
│   │   │   ├── network_monitor/     # Telemetry & bandwidth estimation (planned)
│   │   │   └── decision_engine/     # Segment-level quality allocator (planned)
│   │   ├── database/                # Database connection & models (planned)
│   │   ├── storage/                 # Storage management services (planned)
│   │   └── config/                  # Application configuration & settings
│   ├── tests/                       # Backend test suite (unittest / pytest)
│   │   ├── test_health.py           # Health check endpoint tests
│   │   └── test_video_upload.py     # Video upload, validation & metadata test suite
│   ├── requirements.txt             # Initial Python dependencies
│   └── .env.example                 # Environment variable templates
├── frontend/                        # React + Vite frontend workspace
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

Run the automated test suite from the repository root:

```bash
python -m unittest discover -s backend/tests -v
```
