# StreamWise Frontend — Final UI Revision

A React + Vite + Tailwind CSS frontend for the StreamWise content-aware adaptive bitrate streaming prototype.

## Requirements
- Node.js 18.18+ (Node 20 LTS recommended)
- npm 9+

## Install and run (Windows PowerShell)
1. Extract this ZIP.
2. Open PowerShell in the extracted `StreamWise_Rebuilt` folder (the folder containing `package.json`).
3. Run:

```powershell
npm install
Copy-Item .env.example .env
npm run dev
```

4. Open `http://localhost:5173`.

If PowerShell says `Missing script: dev`, you are not in the folder containing this project's `package.json`. Run `Get-Location` and `dir`, then `cd` into the folder containing `package.json`.

## Video upload and playback behaviour
- Local MP4, WebM, MOV and OGG files are validated and previewed using a browser object URL.
- Object URLs are revoked when the selected file changes or the component unmounts.
- The player uses the selected local file as its source. Starting an experiment does not replace it with a sample video.
- HLS `.m3u8` playback is a separate source mode and uses Shaka Player.
- Local preview does not upload the file to FastAPI. Implement or connect `POST /api/videos` in `src/services/videoService.js` when the backend upload endpoint is ready.
- HLS playback may require the server to allow CORS requests from the frontend origin.

## Environment
`.env.example`:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_USE_MOCK_API=true
VITE_MAX_UPLOAD_MB=500
```

Set `VITE_USE_MOCK_API=false` only when the backend endpoint is available. Restart Vite after changing `.env`.

## Backend integration contract (proposed; confirm with backend team)
The UI keeps API calls in `src/services/`; do not add `fetch()` calls to page components.

### `POST /api/experiments`
Request:
```json
{
  "name": "Lecture video comparison",
  "source": {
    "type": "local_preview",
    "file_name": "lecture.mp4",
    "file_size_bytes": 123456,
    "mime_type": "video/mp4"
  },
  "algorithm": "streamwise",
  "network_profile": {
    "name": "Stable broadband",
    "bandwidthMbps": 8,
    "latencyMs": 40,
    "packetLossPct": 0
  }
}
```
For HLS sources, `source` is `{ "type": "hls", "manifest_url": "https://..." }`.

Suggested response: `{ "id": "exp_123", "name": "...", "status": "queued", "created_at": "ISO-8601" }`.

### `POST /api/videos`
Multipart form-data field: `file`. Suggested response: `{ "video_id": "...", "manifest_url": "...", "file_name": "..." }`.

### Other integration points
- `POST /api/network/configure` — apply bandwidth, latency and packet loss to actual manifest and segment traffic.
- `GET /api/decisions?experiment_id=...` — segment importance and controller-selected rendition.
- `GET /api/metrics?experiment_id=...` — measured QoE, transferred bytes, bitrate and rebuffering.
- `GET /api/experiments` — archive list.

These are proposed contracts, not assumptions about your teammates' implementation. Agree field names and response schemas with them before connecting. Network sliders only send configuration; they do not throttle browser traffic by themselves.

## Current feature status and verification boundaries

- Local file selection and browser preview are frontend functions; they do not upload the file.
- HLS playback is handled by Shaka Player and depends on a valid manifest, compatible codecs, and CORS.
- Experiment setup in local mode records a local preparation only. It is not a measured streaming experiment.
- The network profile controls are configuration inputs; real traffic shaping requires backend/proxy implementation.
- Segment Intelligence and Analytics require the corresponding backend endpoints and real measurement payloads.
- Experiment Archive currently reflects runs created in the current app session; persistent history requires a backend list endpoint.
- The API paths below are proposed integration contracts. Confirm exact paths, payloads, and response schemas with the backend team.

## Fair experiment requirements
Compare the same source, rendition ladder, network profile, playback duration and player settings. Reset state between runs, repeat trials, and store raw player/network measurements with aligned experiment and segment IDs. Do not use local/mock values as research evidence.

## Structure
- `src/components/` — reusable UI and player
- `src/hooks/` — object URL lifecycle
- `src/services/` — isolated API services
- `src/config.js` — environment configuration
- `src/App.jsx` — workspace screens and state orchestration

## Build
```powershell
npm run build
npm run preview
```
