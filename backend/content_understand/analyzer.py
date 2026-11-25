"""Main Content Understanding pipeline orchestrating frame, scene, complexity, OCR, and scoring."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

_SAFE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RESOLUTION_HINT_RE = re.compile(r"(?<!\d)(\d{3,4})p(?!\d)", re.IGNORECASE)
_STREAM_RES_RE = re.compile(r"RESOLUTION\s*=\s*(\d+)x(\d+)", re.IGNORECASE)
_STREAM_BW_RE = re.compile(r"BANDWIDTH\s*=\s*(\d+)", re.IGNORECASE)

from .complexity_analyzer import analyze_frames_complexity
from .config import AnalysisConfig, get_analysis_config
from .frame_extractor import (
    extract_frames,
    get_ffmpeg_executable,
    get_ffprobe_executable,
    probe_media_duration,
)
from .importance_scorer import calculate_importance_score, infer_content_type_hint
from .models import (
    SegmentAnalysis,
    SegmentInput,
    SegmentMetadata,
    VideoAnalysisResult,
)
from .ocr_analyzer import analyze_frames_text_density, is_tesseract_available
from .scene_detector import detect_segment_scene_change


def _natural_sort_key(path: Path) -> Tuple:
    """Sort filenames naturally so seg_2.ts precedes seg_10.ts."""
    parts = re.split(r"(\d+)", path.stem)
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)


def validate_video_id(video_id: str) -> str:
    """Ensure video_id is a safe single identifier without path traversal."""
    if (
        not video_id
        or "/" in video_id
        or "\\" in video_id
        or ".." in video_id
        or not _SAFE_VIDEO_ID_RE.fullmatch(video_id)
    ):
        raise ValueError(f"Invalid video_id '{video_id}'.")
    return video_id


def _resolution_hint(path: Path, base_dir: Optional[Path] = None) -> int:
    """Extract numeric height hint (e.g., 720 from '720p') from the relative rendition path."""
    target = path
    if base_dir is not None:
        try:
            target = path.relative_to(base_dir)
        except ValueError:
            target = path
    matches = _RESOLUTION_HINT_RE.findall(str(target))
    return int(matches[-1]) if matches else 0


def _resolve_playlist_entry(playlist_dir: Path, raw_uri: str, playlist_path: Path) -> Optional[Path]:
    """Resolve a playlist URI safely inside playlist_dir, rejecting traversal or remote URLs."""
    clean_uri = raw_uri.split("?", 1)[0].split("#", 1)[0].strip()
    if not clean_uri or "://" in clean_uri:
        return None
    if Path(clean_uri).is_absolute():
        raise ValueError(f"Playlist {playlist_path} references absolute path: {raw_uri}")
    resolved = (playlist_dir / clean_uri).resolve()
    if not resolved.is_relative_to(playlist_dir):
        raise ValueError(f"Playlist {playlist_path} references path outside directory: {raw_uri}")
    return resolved


def _parse_master_m3u8_variants(playlist_path: Path) -> List[Path]:
    """Return variant media playlist paths from an HLS master playlist, highest quality first."""
    playlist_dir = playlist_path.parent.resolve()
    lines = playlist_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    variants: List[Tuple[int, int, Path]] = []
    pending_height = 0
    pending_bw = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            res_match = _STREAM_RES_RE.search(line)
            bw_match = _STREAM_BW_RE.search(line)
            pending_height = int(res_match.group(2)) if res_match else 0
            pending_bw = int(bw_match.group(1)) if bw_match else 0
            continue
        if line.startswith("#"):
            continue
        if line.split("?", 1)[0].lower().endswith(".m3u8"):
            resolved = _resolve_playlist_entry(playlist_dir, line, playlist_path)
            if resolved is not None and resolved.is_file():
                height = pending_height or _resolution_hint(resolved)
                variants.append((height, pending_bw, resolved))
            pending_height = 0
            pending_bw = 0

    variants.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in variants]


def _parse_m3u8_segments_with_offsets(playlist_path: Path) -> List[Tuple[Path, float, float]]:
    """Extract ordered (segment_path, duration_sec, start_offset_sec) entries from a media HLS playlist."""
    playlist_dir = playlist_path.parent.resolve()
    entries: List[Tuple[Path, float, float]] = []
    pending_duration: Optional[float] = None
    cursor = 0.0

    for raw_line in playlist_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            raw_val = line[len("#EXTINF:") :].split(",", 1)[0].strip()
            try:
                pending_duration = float(raw_val)
            except ValueError:
                pending_duration = None
            continue
        if line.startswith("#"):
            continue
        if line.split("?", 1)[0].lower().endswith(".m3u8"):
            # Master playlist line, not a media segment playlist
            return []

        seg_path = _resolve_playlist_entry(playlist_dir, line, playlist_path)
        duration = pending_duration if pending_duration is not None else 0.0
        if seg_path is not None and seg_path.is_file():
            entries.append((seg_path, duration, cursor))
        else:
            logger.warning("Playlist %s references missing segment file: %s", playlist_path, seg_path or line)

        if duration > 0.0:
            cursor += duration
        pending_duration = None

    return entries


def _parse_m3u8_segments(playlist_path: Path) -> List[Tuple[Path, float]]:
    """Extract ordered (segment_path, duration_sec) entries from a media HLS playlist."""
    return [(path, duration) for path, duration, _ in _parse_m3u8_segments_with_offsets(playlist_path)]


class ContentAnalyzer:
    """Orchestrates segment discovery and content analysis for a processed video."""

    def __init__(self, config: Optional[AnalysisConfig] = None) -> None:
        self.config = config or get_analysis_config()

    def get_video_processed_dir(self, video_id: str) -> Path:
        """Return the storage directory for a processed video."""
        safe_id = validate_video_id(video_id)
        root = self.config.resolve_processed_root().resolve()
        candidate = (root / safe_id).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"Invalid video_id '{video_id}'.")
        return candidate

    def get_metadata_path(
        self, video_id: str, output_path: Optional[Path] = None
    ) -> Path:
        """Return the target path for content_metadata.json."""
        if output_path is not None:
            return output_path
        return self.get_video_processed_dir(video_id) / self.config.metadata_filename

    def _discover_playlists_in_dir(self, directory: Path) -> Optional[List[SegmentInput]]:
        """Discover segments from master or variant .m3u8 playlists, preferring highest quality."""
        top_playlists = sorted(
            directory.glob("*.m3u8"),
            key=lambda p: (-_resolution_hint(p, directory), p.name),
        )
        nested_playlists = sorted(
            (p for p in directory.rglob("*.m3u8") if p not in top_playlists),
            key=lambda p: (-_resolution_hint(p, directory), str(p)),
        )

        for candidate_group in (top_playlists, nested_playlists):
            # 1. Prefer master playlists so we select highest resolution/bandwidth variant
            for playlist in candidate_group:
                variants = _parse_master_m3u8_variants(playlist)
                for variant in variants:
                    parsed = _parse_m3u8_segments_with_offsets(variant)
                    if parsed:
                        return self._build_segment_inputs(parsed)

            # 2. Fall back to direct media playlists ordered by resolution hint descending
            for playlist in candidate_group:
                parsed = _parse_m3u8_segments_with_offsets(playlist)
                if parsed:
                    return self._build_segment_inputs(parsed)
        return None

    def discover_segments(
        self,
        video_id: str,
        segments_dir: Optional[Path] = None,
    ) -> List[SegmentInput]:
        """Discover ordered video segments and compute aligned start/end timestamps."""
        validate_video_id(video_id)
        search_dirs: List[Path] = []
        if segments_dir is not None:
            search_dirs.append(segments_dir)
        else:
            video_dir = self.get_video_processed_dir(video_id)
            search_dirs.extend([video_dir / self.config.segments_subdir, video_dir])

        existing_dirs = [d for d in search_dirs if d.is_dir()]
        if not existing_dirs:
            raise FileNotFoundError(
                f"No processed segment directory found for video_id '{video_id}'."
            )

        # 1. Prefer media HLS playlist (.m3u8) when available for authoritative durations
        for directory in existing_dirs:
            from_playlist = self._discover_playlists_in_dir(directory)
            if from_playlist:
                return from_playlist

        # 2. Fallback to scanning segment files directly (prefer primary format & highest rendition dir)
        raw_files: List[Path] = []
        for directory in existing_dirs:
            candidate_dirs = [directory] + sorted(
                (sub for sub in directory.iterdir() if sub.is_dir()),
                key=lambda sub: (-_resolution_hint(sub, directory), sub.name),
            )
            for cand_dir in candidate_dirs:
                for ext in ("*.ts", "*.m4s", "*.mp4"):
                    found = list(cand_dir.glob(ext))
                    if found:
                        raw_files = found
                        break
                if raw_files:
                    break
            if raw_files:
                break

        unique_sorted = sorted(set(raw_files), key=_natural_sort_key)
        if not unique_sorted:
            raise FileNotFoundError(
                f"No segment media files (.ts/.m4s/.mp4) found for video_id '{video_id}'."
            )

        probed: List[Tuple[Path, float]] = []
        for seg_path in unique_sorted:
            duration = None
            try:
                duration = probe_media_duration(
                    seg_path,
                    timeout_sec=self.config.ffprobe_timeout_sec,
                    custom_ffprobe_path=self.config.ffprobe_path,
                )
            except RuntimeError:
                duration = None
            probed.append(
                (seg_path, duration if duration is not None else self.config.default_segment_duration_sec)
            )

        return self._build_segment_inputs(probed)

    def _build_segment_inputs(
        self, entries: Sequence[Union[Tuple[Path, float], Tuple[Path, float, float]]]
    ) -> List[SegmentInput]:
        """Build SegmentInput records with cumulative or playlist-offset timestamps."""
        segments: List[SegmentInput] = []
        seen_ids: set[str] = set()
        cursor = 0.0

        for idx, entry in enumerate(entries):
            if len(entry) == 3:
                seg_path, raw_duration, start_offset = entry
                effective_start = max(cursor, start_offset)
            else:
                seg_path, raw_duration = entry
                effective_start = cursor

            duration = raw_duration if raw_duration > 0.0 else self.config.default_segment_duration_sec
            start_time = round(effective_start, 4)
            end_time = round(effective_start + duration, 4)
            cursor = end_time

            base_id = seg_path.stem or f"seg_{idx:03d}"
            seg_id = base_id
            suffix_idx = idx
            while seg_id in seen_ids:
                seg_id = f"{base_id}_{suffix_idx:03d}"
                suffix_idx += 1
            seen_ids.add(seg_id)

            segments.append(
                SegmentInput(
                    segment_id=seg_id,
                    segment_index=idx,
                    file_path=seg_path,
                    start_time_sec=start_time,
                    end_time_sec=end_time,
                    duration_sec=round(duration, 4),
                )
            )
        return segments

    def analyze_segment(
        self,
        video_id: str,
        segment: SegmentInput,
        frames_dir: Path,
        prev_segment_last_frame: Optional[Path] = None,
        frames_per_segment: Optional[int] = None,
    ) -> Tuple[SegmentMetadata, Optional[Path]]:
        """Analyze a single segment and return its metadata plus its final extracted frame."""
        num_frames = (
            frames_per_segment if frames_per_segment is not None else self.config.frames_per_segment
        )
        frames = extract_frames(
            segment_path=segment.file_path,
            output_dir=frames_dir,
            num_frames=num_frames,
            image_format=self.config.frame_image_format,
            timeout_sec=self.config.ffmpeg_timeout_sec,
            default_duration_sec=segment.duration_sec or self.config.default_segment_duration_sec,
            custom_ffmpeg_path=self.config.ffmpeg_path,
            custom_ffprobe_path=self.config.ffprobe_path,
        )
        if not frames:
            raise RuntimeError(
                f"No frames extracted for segment '{segment.segment_id}' ({segment.file_path})."
            )

        visual_complexity = analyze_frames_complexity(
            frames,
            blur_kernel_size=self.config.blur_kernel_size,
            canny_threshold1=self.config.canny_threshold1,
            canny_threshold2=self.config.canny_threshold2,
            normalization_factor=self.config.complexity_normalization_factor,
        )

        ocr_summary = analyze_frames_text_density(
            frames,
            tesseract_cmd=self.config.tesseract_cmd,
            confidence_threshold=self.config.ocr_confidence_threshold,
            text_density_threshold=self.config.text_density_threshold,
            word_count_threshold=self.config.significant_word_count_threshold,
            text_sample_max_chars=self.config.text_sample_max_chars,
            fallback_mode=self.config.ocr_fallback_mode,
        )

        scene_change = detect_segment_scene_change(
            frames,
            prev_segment_last_frame=prev_segment_last_frame,
            threshold=self.config.scene_change_threshold,
        )

        importance_score = calculate_importance_score(
            visual_complexity=visual_complexity,
            text_density=ocr_summary["text_density"],
            scene_change=scene_change,
            has_significant_text=ocr_summary["has_significant_text"],
            weights=self.config.weights.as_dict(),
        )

        content_hint = infer_content_type_hint(
            visual_complexity=visual_complexity,
            text_density=ocr_summary["text_density"],
            has_significant_text=ocr_summary["has_significant_text"],
            scene_change=scene_change,
            detected_text_sample=ocr_summary["detected_text_sample"],
            config=self.config,
        )

        analysis = SegmentAnalysis(
            visual_complexity=visual_complexity,
            text_density=ocr_summary["text_density"],
            scene_change_detected=scene_change,
            detected_text_sample=ocr_summary["detected_text_sample"],
            word_count=ocr_summary["word_count"],
            has_significant_text=ocr_summary["has_significant_text"],
            importance_score=importance_score,
            content_type_hint=content_hint,
            ocr_engine=ocr_summary["ocr_engine"],
        )

        metadata = SegmentMetadata(
            video_id=video_id,
            segment_id=segment.segment_id,
            segment_index=segment.segment_index,
            start_time_sec=segment.start_time_sec,
            end_time_sec=segment.end_time_sec,
            duration_sec=segment.duration_sec,
            analysis=analysis,
            frames_analyzed=len(frames),
            analysis_timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            analyzer_version=self.config.analyzer_version,
        )
        return metadata, frames[-1]

    def analyze_segments(
        self,
        video_id: str,
        segments: Sequence[SegmentInput],
        output_path: Optional[Path] = None,
        frames_per_segment: Optional[int] = None,
        save_output: bool = True,
        total_timeout_sec: Optional[float] = None,
    ) -> VideoAnalysisResult:
        """Run full pipeline across an ordered sequence of segments."""
        if not segments:
            raise ValueError(f"Cannot analyze empty segment list for video_id '{video_id}'.")
        validate_video_id(video_id)
        if len(segments) > self.config.max_segments:
            raise ValueError(
                f"Segment count ({len(segments)}) exceeds max_segments limit ({self.config.max_segments}) "
                f"for video_id '{video_id}'."
            )

        target_output: Optional[Path] = None
        if save_output:
            target_output = self.get_metadata_path(video_id, output_path=output_path)
            if target_output.exists() and target_output.is_dir():
                raise ValueError(f"Metadata output path cannot be a directory: {target_output}")

        effective_timeout = (
            total_timeout_sec
            if total_timeout_sec is not None
            else self.config.analysis_timeout_sec
        )
        started_at = time.monotonic()
        logger.info("Starting content analysis for video_id='%s' (%d segments).", video_id, len(segments))

        results: List[SegmentMetadata] = []
        with tempfile.TemporaryDirectory(prefix=f"streamwise_frames_{video_id}_") as tmp_dir:
            tmp_root = Path(tmp_dir)
            prev_last_frame: Optional[Path] = None

            for segment in segments:
                if effective_timeout is not None and (time.monotonic() - started_at) > effective_timeout:
                    raise TimeoutError(
                        f"Content analysis timed out after {effective_timeout:.1f}s for video_id '{video_id}'."
                    )

                seg_frames_dir = tmp_root / f"seg_{segment.segment_index:04d}"
                seg_meta, last_frame = self.analyze_segment(
                    video_id=video_id,
                    segment=segment,
                    frames_dir=seg_frames_dir,
                    prev_segment_last_frame=prev_last_frame,
                    frames_per_segment=frames_per_segment,
                )
                results.append(seg_meta)

                if last_frame is not None and last_frame.is_file():
                    stable_prev = tmp_root / f"_prev_last_frame{last_frame.suffix}"
                    shutil.copy2(last_frame, stable_prev)
                    prev_last_frame = stable_prev
                else:
                    prev_last_frame = None

        video_result = VideoAnalysisResult(
            video_id=video_id,
            total_segments=len(results),
            analysis_version=self.config.analyzer_version,
            segments=results,
        )

        if save_output:
            target = target_output if target_output is not None else self.get_metadata_path(video_id, output_path=output_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(video_result.model_dump(mode="json"), indent=2)
            tmp_file_path: Optional[Path] = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as tmp_file:
                    tmp_file.write(payload)
                    tmp_file_path = Path(tmp_file.name)
                os.replace(tmp_file_path, target)
            except Exception:
                if tmp_file_path is not None:
                    tmp_file_path.unlink(missing_ok=True)
                raise
            logger.info("Saved content metadata for video_id='%s' to %s.", video_id, target)

        return video_result

    def analyze_video(
        self,
        video_id: str,
        segments_dir: Optional[Path] = None,
        output_path: Optional[Path] = None,
        frames_per_segment: Optional[int] = None,
        save_output: bool = True,
    ) -> VideoAnalysisResult:
        """Discover segments for video_id, analyze them, and persist metadata JSON."""
        segments = self.discover_segments(video_id=video_id, segments_dir=segments_dir)
        return self.analyze_segments(
            video_id=video_id,
            segments=segments,
            output_path=output_path,
            frames_per_segment=frames_per_segment,
            save_output=save_output,
        )

    def load_metadata(
        self, video_id: str, metadata_path: Optional[Path] = None
    ) -> VideoAnalysisResult:
        """Load pre-computed VideoAnalysisResult from disk."""
        target = self.get_metadata_path(video_id, output_path=metadata_path)
        if not target.is_file():
            raise FileNotFoundError(
                f"Content analysis metadata not found for video_id '{video_id}' at {target}."
            )
        raw = json.loads(target.read_text(encoding="utf-8"))
        return VideoAnalysisResult.model_validate(raw)

    def load_segment_metadata(
        self,
        video_id: str,
        segment_id: str,
        metadata_path: Optional[Path] = None,
    ) -> SegmentMetadata:
        """Load pre-computed metadata for a single segment."""
        video_meta = self.load_metadata(video_id, metadata_path=metadata_path)
        for segment in video_meta.segments:
            if segment.segment_id == segment_id:
                return segment
        raise KeyError(
            f"Segment '{segment_id}' not found in metadata for video_id '{video_id}'."
        )

    def check_capabilities(self) -> dict:
        """Inspect runtime availability of FFmpeg, FFprobe, PySceneDetect, and Tesseract."""
        ffmpeg_ok = True
        ffprobe_ok = True
        try:
            get_ffmpeg_executable(self.config.ffmpeg_path)
        except RuntimeError:
            ffmpeg_ok = False
        try:
            get_ffprobe_executable(self.config.ffprobe_path)
        except RuntimeError:
            ffprobe_ok = False

        try:
            import scenedetect  # noqa: F401
            scenedetect_ok = True
        except ImportError:
            scenedetect_ok = False

        return {
            "analyzer_version": self.config.analyzer_version,
            "ffmpeg_available": ffmpeg_ok,
            "ffprobe_available": ffprobe_ok,
            "scenedetect_available": scenedetect_ok,
            "tesseract_available": is_tesseract_available(self.config.tesseract_cmd),
            "ocr_fallback_mode": self.config.ocr_fallback_mode,
        }
