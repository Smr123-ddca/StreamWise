"""Shared synthetic educational video and frame generators for tests and examples."""

from pathlib import Path
import subprocess
from typing import Callable, List
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..frame_extractor import get_ffmpeg_executable


def _draw_text_lines(
    img_rgb: np.ndarray,
    lines: List[str],
    start_x: int = 40,
    start_y: int = 40,
    line_height: int = 34,
    color: tuple = (20, 20, 20),
    font_size: int = 20,
) -> np.ndarray:
    """Render crisp text lines onto an RGB numpy image using Pillow."""
    pil_img = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(pil_img)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    y = start_y
    for line in lines:
        draw.text((start_x, y), line, fill=color, font=font)
        y += line_height
    return np.asarray(pil_img)


def make_blank_frame(path: Path, width: int = 640, height: int = 360, value: int = 255) -> Path:
    """Create a uniform solid-color image frame."""
    frame = np.full((height, width, 3), value, dtype=np.uint8)
    cv2.imwrite(str(path), frame)
    return path


def make_slide_frame(
    path: Path,
    title: str,
    bullets: List[str],
    bg_color: tuple = (248, 250, 252),
    width: int = 640,
    height: int = 360,
) -> Path:
    """Create a realistic lecture presentation slide frame with title and bullets."""
    img = np.full((height, width, 3), bg_color, dtype=np.uint8)
    # Header bar
    img[0:64, :] = (30, 58, 138)
    img = _draw_text_lines(img, [title], start_x=28, start_y=18, color=(255, 255, 255), font_size=24)
    img = _draw_text_lines(
        img,
        [f"• {b}" for b in bullets],
        start_x=36,
        start_y=92,
        line_height=38,
        color=(25, 25, 35),
        font_size=18,
    )
    cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path


def make_code_frame(
    path: Path,
    code_lines: List[str],
    width: int = 640,
    height: int = 360,
) -> Path:
    """Create a dark-theme IDE screen frame with dense code text."""
    img = np.full((height, width, 3), (24, 26, 32), dtype=np.uint8)
    # Sidebar and top tab bar
    img[:, 0:70] = (33, 37, 45)
    img[0:32, :] = (40, 44, 52)
    img = _draw_text_lines(
        img,
        code_lines,
        start_x=86,
        start_y=46,
        line_height=26,
        color=(212, 224, 240),
        font_size=16,
    )
    cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path


def make_explainer_frame(
    path: Path,
    step_idx: int,
    variant: int = 0,
    width: int = 640,
    height: int = 360,
) -> Path:
    """Create an animated explainer frame with geometric diagrams and labels."""
    bg = (245, 247, 250) if variant % 2 == 0 else (20, 40, 65)
    fg = (30, 41, 59) if variant % 2 == 0 else (240, 248, 255)
    img = np.full((height, width, 3), bg, dtype=np.uint8)

    offset = (step_idx * 25) % 180
    cv2.rectangle(img, (60 + offset, 90), (200 + offset, 210), (59, 130, 246), 3)
    cv2.circle(img, (440, 150), 55, (16, 185, 129), 3)
    cv2.arrowedLine(img, (210 + offset, 150), (375, 150), (239, 68, 68), 3)
    for k in range(6):
        y_line = 250 + k * 14
        cv2.line(img, (80, y_line), (560 - k * 30, y_line), fg, 2)

    img = _draw_text_lines(
        img,
        [f"Pipeline Stage {variant + 1}: Packet Flow"],
        start_x=120,
        start_y=28,
        color=fg,
        font_size=20,
    )
    cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path


def render_ts_segment(
    output_ts: Path,
    frame_renderer: Callable[[int], np.ndarray],
    duration_sec: float = 2.0,
    fps: int = 5,
    width: int = 640,
    height: int = 360,
) -> Path:
    """Encode an in-memory sequence of RGB frames directly into an MPEG-TS (.ts) segment."""
    output_ts.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_cmd = get_ffmpeg_executable()
    total_frames = max(int(round(duration_sec * fps)), 2)

    cmd = [
        ffmpeg_cmd,
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "mpeg2video",
        "-q:v",
        "3",
        "-f",
        "mpegts",
        str(output_ts),
    ]

    raw_bytes = b"".join(frame_renderer(i).tobytes() for i in range(total_frames))
    result = subprocess.run(
        cmd,
        input=raw_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        shell=False,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg TS generation failed: {result.stderr.decode(errors='ignore')}")
    return output_ts


def create_sample_educational_segments(
    target_dir: Path,
    sample_kind: str,
    duration_sec: float = 2.0,
) -> Path:
    """Create a 3-segment HLS directory (.ts files + playlist.m3u8) for a sample category."""
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp_img = target_dir / "_tmp_render.png"

    def _load_rgb(path: Path) -> np.ndarray:
        bgr = cv2.imread(str(path))
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    try:
        if sample_kind == "lecture_slides":
            render_ts_segment(
                target_dir / "seg_000.ts",
                lambda _: _load_rgb(make_blank_frame(tmp_img, value=235)),
                duration_sec=duration_sec,
            )
            render_ts_segment(
                target_dir / "seg_001.ts",
                lambda _: _load_rgb(
                    make_slide_frame(
                        tmp_img,
                        "Adaptive Bitrate Streaming",
                        [
                            "Traditional ABR ignores visual scene complexity",
                            "Static slides require lower bitrate than action",
                            "Text legibility depends on spatial detail",
                            "Decision engine allocates bits per segment",
                        ],
                    )
                ),
                duration_sec=duration_sec,
            )
            render_ts_segment(
                target_dir / "seg_002.ts",
                lambda _: _load_rgb(
                    make_slide_frame(
                        tmp_img,
                        "Summary & Key Takeaways",
                        [
                            "Segment-level content metadata drives quality",
                            "Scene transitions trigger bitrate reassessment",
                        ],
                        bg_color=(230, 240, 255),
                    )
                ),
                duration_sec=duration_sec,
            )

        elif sample_kind == "coding_tutorial":
            render_ts_segment(
                target_dir / "seg_000.ts",
                lambda _: _load_rgb(make_blank_frame(tmp_img, value=28)),
                duration_sec=duration_sec,
            )
            render_ts_segment(
                target_dir / "seg_001.ts",
                lambda _: _load_rgb(
                    make_code_frame(
                        tmp_img,
                        [
                            "import numpy as np",
                            "import cv2",
                            "",
                            "def calculate_loss(y_pred, y_true):",
                            "    diff = y_pred - y_true",
                            "    return float(np.mean(diff ** 2))",
                            "",
                            "def compute_complexity(frame):",
                            "    edges = cv2.Canny(frame, 50, 150)",
                            "    return float(edges.mean())",
                        ],
                    )
                ),
                duration_sec=duration_sec,
            )
            render_ts_segment(
                target_dir / "seg_002.ts",
                lambda _: _load_rgb(
                    make_code_frame(
                        tmp_img,
                        [
                            "class SegmentOptimizer:",
                            "    def __init__(self, budget_kbps: int):",
                            "        self.budget = budget_kbps",
                            "    def select_rendition(self, score: float):",
                            "        if score > 0.75:",
                            "            return '720p'",
                            "        if score > 0.40:",
                            "            return '480p'",
                            "        return '360p'",
                        ],
                    )
                ),
                duration_sec=duration_sec,
            )

        elif sample_kind == "animated_explainer":
            render_ts_segment(
                target_dir / "seg_000.ts",
                lambda i: _load_rgb(make_explainer_frame(tmp_img, step_idx=i, variant=0)),
                duration_sec=duration_sec,
            )
            render_ts_segment(
                target_dir / "seg_001.ts",
                lambda i: _load_rgb(make_explainer_frame(tmp_img, step_idx=i, variant=1)),
                duration_sec=duration_sec,
            )
            render_ts_segment(
                target_dir / "seg_002.ts",
                lambda i: _load_rgb(make_explainer_frame(tmp_img, step_idx=i + 3, variant=2)),
                duration_sec=duration_sec,
            )
        else:
            raise ValueError(f"Unknown sample_kind: {sample_kind}")
    finally:
        tmp_img.unlink(missing_ok=True)

    playlist = target_dir / "playlist.m3u8"
    playlist.write_text(
        "\n".join(
            [
                "#EXTM3U",
                "#EXT-X-VERSION:3",
                f"#EXT-X-TARGETDURATION:{int(np.ceil(duration_sec))}",
                f"#EXTINF:{duration_sec:.3f},",
                "seg_000.ts",
                f"#EXTINF:{duration_sec:.3f},",
                "seg_001.ts",
                f"#EXTINF:{duration_sec:.3f},",
                "seg_002.ts",
                "#EXT-X-ENDLIST",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return target_dir
