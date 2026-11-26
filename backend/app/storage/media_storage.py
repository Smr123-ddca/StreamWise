"""Secure HLS media storage resolution, playlist validation, and HTTP byte-range delivery."""

import hashlib
import re
from email.utils import formatdate
from pathlib import Path
from typing import Iterator, Optional, Tuple
from urllib.parse import urlparse
from fastapi import HTTPException, status
from fastapi.responses import Response, StreamingResponse

from ..modules.video_processing.service import resolve_processed_directory, validate_video_id

_RENDITION_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{2,16}$")
_SEGMENT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+\.ts$")
_RANGE_HEADER_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

PLAYLIST_MIME_TYPE = "application/vnd.apple.mpegurl"
SEGMENT_MIME_TYPE = "video/mp2t"
PLAYLIST_CACHE_CONTROL = "no-cache, must-revalidate"
SEGMENT_CACHE_CONTROL = "public, max-age=3600"
STREAM_CHUNK_SIZE = 64 * 1024  # 64 KB chunks


def _assert_no_symlinks(target: Path, stop_at: Path) -> None:
    """Reject symlinks anywhere from target up to stop_at."""
    current = target
    stop_resolved = stop_at.resolve()
    while True:
        if current.is_symlink():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Symbolic links are not permitted in media storage.",
            )
        if current.resolve() == stop_resolved:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent


def _validate_safe_component(value: str, pattern: re.Pattern[str], label: str) -> str:
    """Validate a single path segment against a strict allowlist."""
    if not value or value.startswith(".") or not pattern.match(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {label} identifier.",
        )
    return value


def _validate_playlist_uri(
    uri: str,
    playlist_dir: Path,
    hls_root: Path,
    required_suffix: str,
) -> Path:
    """Validate an HLS playlist URI reference is relative, safe, and stays inside hls_root."""
    if not uri or uri.startswith(("/", "\\")):
        raise HTTPException(
            status_code=422,
            detail="Playlist contains an invalid absolute path URI.",
        )

    parsed = urlparse(uri)
    if parsed.scheme or parsed.netloc or (len(uri) >= 2 and uri[1] == ":"):
        raise HTTPException(
            status_code=422,
            detail="Playlist contains an unsupported URI scheme or external reference.",
        )

    if "\\" in uri or ".." in Path(uri).parts:
        raise HTTPException(
            status_code=422,
            detail="Playlist contains unsafe path traversal components.",
        )

    if not uri.lower().endswith(required_suffix):
        raise HTTPException(
            status_code=422,
            detail=f"Playlist references unexpected file type: '{uri}'.",
        )

    candidate = playlist_dir / uri
    resolved = candidate.resolve()
    hls_resolved = hls_root.resolve()
    if not resolved.is_relative_to(hls_resolved) or resolved == hls_resolved:
        raise HTTPException(
            status_code=422,
            detail="Playlist references a file outside the HLS root directory.",
        )
    _assert_no_symlinks(candidate, hls_resolved)
    return resolved


def validate_master_playlist_bytes(content: str, hls_root: Path) -> None:
    """Validate master.m3u8 syntax and ensure all rendition playlist URIs stay inside hls_root."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise HTTPException(
            status_code=422,
            detail="Corrupted master playlist: missing #EXTM3U header.",
        )

    stream_inf_seen = False
    rendition_uris: list[str] = []
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF:"):
            stream_inf_seen = True
            continue
        if line.startswith("#"):
            continue
        _validate_playlist_uri(line, hls_root, hls_root, ".m3u8")
        rendition_uris.append(line)

    if not stream_inf_seen or not rendition_uris:
        raise HTTPException(
            status_code=422,
            detail="Corrupted master playlist: no rendition streams declared.",
        )


def validate_media_playlist_bytes(content: str, rendition_dir: Path, hls_root: Path) -> None:
    """Validate rendition playlist.m3u8 syntax and verify all segment URIs stay inside hls_root."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise HTTPException(
            status_code=422,
            detail="Corrupted rendition playlist: missing #EXTM3U header.",
        )

    has_extinf = False
    segment_uris: list[str] = []
    for line in lines:
        if line.startswith("#EXTINF:"):
            has_extinf = True
            continue
        if line.startswith("#"):
            continue
        _validate_playlist_uri(line, rendition_dir, hls_root, ".ts")
        segment_uris.append(line)

    if not has_extinf or not segment_uris:
        raise HTTPException(
            status_code=422,
            detail="Corrupted rendition playlist: no media segments declared.",
        )


def _parse_byte_range(range_header: str, file_size: int) -> Tuple[int, int]:
    """Parse an HTTP Range header (`bytes=start-end`) against file_size.

    Raises:
        HTTPException(416): If the range syntax is invalid or unsatisfiable.
    """
    match = _RANGE_HEADER_RE.match(range_header.strip())
    if not match:
        raise HTTPException(
            status_code=416,
            detail="Invalid or unsupported Range header.",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    start_str, end_str = match.group(1), match.group(2)
    if not start_str and not end_str:
        raise HTTPException(
            status_code=416,
            detail="Empty byte range.",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    if not start_str:
        # Suffix byte range: bytes=-N (last N bytes)
        suffix_len = int(end_str)
        if suffix_len <= 0:
            raise HTTPException(
                status_code=416,
                detail="Suffix range length must be positive.",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        start = max(0, file_size - suffix_len)
        end = file_size - 1
    else:
        start = int(start_str)
        if start >= file_size:
            raise HTTPException(
                status_code=416,
                detail="Requested range start exceeds file size.",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        end = int(end_str) if end_str else (file_size - 1)
        if end < start:
            raise HTTPException(
                status_code=416,
                detail="Range end cannot precede range start.",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        end = min(end, file_size - 1)

    return start, end


def _iter_file_range(file_path: Path, start: int, length: int) -> Iterator[bytes]:
    """Stream an exact byte slice from disk in fixed chunks."""
    remaining = length
    with open(file_path, "rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


class MediaDeliveryStorage:
    """Secure, scoped resolver and HTTP delivery handler for HLS playlists and segments."""

    def __init__(self, storage_dir: str) -> None:
        self.storage_dir = storage_dir

    def _get_hls_root(self, video_id: str) -> Tuple[str, Path, Path]:
        canonical_id = validate_video_id(video_id)
        processed_dir = resolve_processed_directory(self.storage_dir)
        processed_resolved = processed_dir.resolve()
        video_dir = (processed_dir / canonical_id).resolve()

        if not video_dir.is_relative_to(processed_resolved) or video_dir == processed_resolved:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid video ID path.")

        hls_root = video_dir / "hls"
        if not hls_root.exists() or not hls_root.is_dir():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"HLS stream not found for video ID '{canonical_id}'.",
            )
        _assert_no_symlinks(hls_root, processed_resolved)
        return canonical_id, hls_root.resolve(), processed_resolved

    def _resolve_existing_file(self, candidate: Path, hls_root: Path, label: str) -> Path:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(hls_root) or resolved == hls_root:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path escapes HLS root.")
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found.")
        _assert_no_symlinks(candidate, hls_root)
        if candidate.stat().st_size == 0:
            raise HTTPException(
                status_code=422,
                detail=f"{label} is corrupted (0 bytes).",
            )
        return resolved

    def resolve_master_playlist(self, video_id: str) -> Tuple[Path, bytes]:
        """Resolve and validate master.m3u8 for delivery."""
        _, hls_root, _ = self._get_hls_root(video_id)
        master_path = self._resolve_existing_file(hls_root / "master.m3u8", hls_root, "Master playlist")
        raw_bytes = master_path.read_bytes()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=422,
                detail="Corrupted master playlist encoding.",
            ) from exc
        validate_master_playlist_bytes(text, hls_root)
        return master_path, raw_bytes

    def resolve_rendition_playlist(self, video_id: str, rendition: str) -> Tuple[Path, bytes]:
        """Resolve and validate {rendition}/playlist.m3u8 for delivery."""
        _, hls_root, _ = self._get_hls_root(video_id)
        safe_rendition = _validate_safe_component(rendition, _RENDITION_NAME_RE, "rendition")
        rendition_dir = hls_root / safe_rendition
        playlist_path = self._resolve_existing_file(
            rendition_dir / "playlist.m3u8",
            hls_root,
            f"Rendition playlist '{safe_rendition}/playlist.m3u8'",
        )
        raw_bytes = playlist_path.read_bytes()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=422,
                detail="Corrupted rendition playlist encoding.",
            ) from exc
        validate_media_playlist_bytes(text, rendition_dir.resolve(), hls_root)
        return playlist_path, raw_bytes

    def resolve_segment(self, video_id: str, rendition: str, segment_name: str) -> Path:
        """Resolve and validate an MPEG-TS segment inside {rendition}/."""
        _, hls_root, _ = self._get_hls_root(video_id)
        safe_rendition = _validate_safe_component(rendition, _RENDITION_NAME_RE, "rendition")
        safe_segment = _validate_safe_component(segment_name, _SEGMENT_NAME_RE, "segment")
        return self._resolve_existing_file(
            hls_root / safe_rendition / safe_segment,
            hls_root,
            f"Media segment '{safe_rendition}/{safe_segment}'",
        )

    def build_media_response(
        self,
        file_path: Path,
        media_type: str,
        cache_control: str,
        method: str,
        range_header: Optional[str] = None,
        inline_bytes: Optional[bytes] = None,
    ) -> Response:
        """Build an HTTP GET/HEAD response with ETag, Last-Modified, caching, and Range support."""
        stat = file_path.stat()
        file_size = len(inline_bytes) if inline_bytes is not None else stat.st_size
        last_modified = formatdate(stat.st_mtime, usegmt=True)
        etag_seed = f"{stat.st_mtime_ns}:{file_size}:{file_path.name}".encode("utf-8")
        etag = f'"{hashlib.sha256(etag_seed).hexdigest()[:16]}"'

        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": cache_control,
            "ETag": etag,
            "Last-Modified": last_modified,
            "X-Content-Type-Options": "nosniff",
        }

        if range_header:
            start, end = _parse_byte_range(range_header, file_size)
            length = end - start + 1
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            headers["Content-Length"] = str(length)
            if method.upper() == "HEAD":
                return Response(status_code=status.HTTP_206_PARTIAL_CONTENT, headers=headers, media_type=media_type)
            if inline_bytes is not None:
                return Response(
                    content=inline_bytes[start : end + 1],
                    status_code=status.HTTP_206_PARTIAL_CONTENT,
                    headers=headers,
                    media_type=media_type,
                )
            return StreamingResponse(
                _iter_file_range(file_path, start, length),
                status_code=status.HTTP_206_PARTIAL_CONTENT,
                headers=headers,
                media_type=media_type,
            )

        headers["Content-Length"] = str(file_size)
        if method.upper() == "HEAD":
            return Response(status_code=status.HTTP_200_OK, headers=headers, media_type=media_type)

        if inline_bytes is not None:
            return Response(
                content=inline_bytes,
                status_code=status.HTTP_200_OK,
                headers=headers,
                media_type=media_type,
            )

        return StreamingResponse(
            _iter_file_range(file_path, 0, file_size),
            status_code=status.HTTP_200_OK,
            headers=headers,
            media_type=media_type,
        )
