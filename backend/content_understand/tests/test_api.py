"""Tests for Content Understanding API handlers and FastAPI endpoints."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import backend.content_understand.api as cu_api
from backend.content_understand.analyzer import ContentAnalyzer
from backend.content_understand.api import (
    FASTAPI_AVAILABLE,
    AnalysisAPIError,
    analyze_video_handler,
    create_app,
    get_metadata_handler,
    get_segment_metadata_handler,
    health_handler,
)
from backend.content_understand.config import AnalysisConfig
from backend.content_understand.frame_extractor import MediaExtractionError
from backend.content_understand.models import (
    AnalyzeVideoRequest,
    SegmentMetadata,
    VideoAnalysisResult,
)
from backend.content_understand.tests._synthetic_media import create_sample_educational_segments


class ContentUnderstandingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage_dir = self.root / "storage"
        self.config = AnalysisConfig(storage_dir=self.storage_dir, frames_per_segment=2)
        self.analyzer = ContentAnalyzer(config=self.config)

        seg_dir = self.storage_dir / "processed" / "vid_001" / "segments"
        create_sample_educational_segments(seg_dir, sample_kind="lecture_slides", duration_sec=2.0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_handlers_analyze_and_fetch_metadata_contract(self) -> None:
        health = health_handler(analyzer=self.analyzer)
        self.assertEqual(health["status"], "healthy")
        self.assertTrue(health["capabilities"]["ffmpeg_available"])

        # 404 before analysis runs
        with self.assertRaises(AnalysisAPIError) as missing_ctx:
            get_metadata_handler("vid_001", analyzer=self.analyzer)
        self.assertEqual(missing_ctx.exception.status_code, 404)

        result = analyze_video_handler(
            "vid_001",
            request=AnalyzeVideoRequest(frames_per_segment=2),
            analyzer=self.analyzer,
        )
        self.assertIsInstance(result, VideoAnalysisResult)
        self.assertEqual(result.video_id, "vid_001")
        self.assertEqual(result.total_segments, 3)

        loaded = get_metadata_handler("vid_001", analyzer=self.analyzer)
        self.assertEqual(loaded.video_id, "vid_001")
        self.assertEqual(len(loaded.segments), 3)

        seg_meta = get_segment_metadata_handler("vid_001", "seg_001", analyzer=self.analyzer)
        self.assertIsInstance(seg_meta, SegmentMetadata)
        self.assertEqual(seg_meta.segment_id, "seg_001")
        self.assertEqual(seg_meta.segment_index, 1)

        with self.assertRaises(AnalysisAPIError) as seg_missing:
            get_segment_metadata_handler("vid_001", "seg_999", analyzer=self.analyzer)
        self.assertEqual(seg_missing.exception.status_code, 404)

    @unittest.skipUnless(FASTAPI_AVAILABLE, "FastAPI not installed in current Python environment")
    def test_fastapi_endpoints_with_test_client(self) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(create_app(config=self.config))

        res_404 = client.get("/analysis/metadata/vid_001")
        self.assertEqual(res_404.status_code, 404)

        res_post = client.post("/analysis/analyze/vid_001")
        self.assertEqual(res_post.status_code, 200)
        payload = res_post.json()
        self.assertEqual(payload["video_id"], "vid_001")
        self.assertEqual(payload["total_segments"], 3)

        res_seg = client.get("/analysis/metadata/vid_001/seg_001")
        self.assertEqual(res_seg.status_code, 200)
        self.assertEqual(res_seg.json()["segment_id"], "seg_001")

    def test_timeout_maps_to_504_and_lazy_exports_work(self) -> None:
        fake_analyzer = MagicMock()
        fake_analyzer.analyze_video.side_effect = TimeoutError("timed out")
        with self.assertRaises(AnalysisAPIError) as exc_ctx:
            analyze_video_handler("vid_001", analyzer=fake_analyzer)
        self.assertEqual(exc_ctx.exception.status_code, 504)

        self.assertNotIn("router", cu_api.__dict__)
        self.assertNotIn("app", cu_api.__dict__)
        if FASTAPI_AVAILABLE:
            self.assertIsNotNone(cu_api.router)
            self.assertIsNotNone(cu_api.app)

    @unittest.skipUnless(FASTAPI_AVAILABLE, "FastAPI not installed in current Python environment")
    def test_main_app_mounts_analysis_routes_with_shared_storage(self) -> None:
        from fastapi.testclient import TestClient

        from backend.app.config.settings import Settings, get_settings
        from backend.app.main import app as main_app

        test_settings = Settings(storage_dir=str(self.storage_dir))
        main_app.dependency_overrides[get_settings] = lambda: test_settings
        try:
            client = TestClient(main_app)
            health_res = client.get("/analysis/health")
            self.assertEqual(health_res.status_code, 200)
            self.assertEqual(health_res.json()["status"], "healthy")

            analyze_res = client.post("/analysis/analyze/vid_001")
            self.assertEqual(analyze_res.status_code, 200)
            self.assertEqual(analyze_res.json()["video_id"], "vid_001")
            self.assertEqual(analyze_res.json()["total_segments"], 3)

            meta_res = client.get("/analysis/metadata/vid_001")
            self.assertEqual(meta_res.status_code, 200)
            self.assertEqual(len(meta_res.json()["segments"]), 3)
        finally:
            main_app.dependency_overrides.clear()

    @unittest.skipUnless(FASTAPI_AVAILABLE, "FastAPI not installed in current Python environment")
    def test_standalone_router_schema_does_not_expose_config_and_parses_request_body(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        standalone_app = FastAPI()
        standalone_app.include_router(cu_api.build_router())
        schema = standalone_app.openapi()

        self.assertIsNone(schema["paths"]["/analysis/health"]["get"].get("requestBody"))
        self.assertIsNone(schema["paths"]["/analysis/metadata/{video_id}"]["get"].get("requestBody"))
        post_schema = schema["paths"]["/analysis/analyze/{video_id}"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertIn("AnalyzeVideoRequest", str(post_schema))
        self.assertNotIn("AnalysisConfig", str(schema))

        with patch.dict("os.environ", {"STREAMWISE_STORAGE_DIR": str(self.storage_dir)}):
            client = TestClient(standalone_app)
            res = client.post("/analysis/analyze/vid_001", json={"frames_per_segment": 1})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["segments"][0]["frames_analyzed"], 1)

    def test_path_traversal_and_media_extraction_error_status_codes(self) -> None:
        with self.assertRaises(AnalysisAPIError) as bad_id:
            analyze_video_handler("..", analyzer=self.analyzer)
        self.assertEqual(bad_id.exception.status_code, 422)

        with self.assertRaises(AnalysisAPIError) as bad_seg_dir:
            analyze_video_handler(
                "vid_001",
                request=AnalyzeVideoRequest(segments_dir=str(self.root.parent)),
                analyzer=self.analyzer,
            )
        self.assertEqual(bad_seg_dir.exception.status_code, 422)

        with self.assertRaises(AnalysisAPIError) as bad_out:
            analyze_video_handler(
                "vid_001",
                request=AnalyzeVideoRequest(output_path=str(self.root / "outside.json")),
                analyzer=self.analyzer,
            )
        self.assertEqual(bad_out.exception.status_code, 422)

        with self.assertRaises(AnalysisAPIError) as cross_vid_out:
            analyze_video_handler(
                "vid_001",
                request=AnalyzeVideoRequest(output_path="../vid_002/content_metadata.json"),
                analyzer=self.analyzer,
            )
        self.assertEqual(cross_vid_out.exception.status_code, 422)

        with self.assertRaises(AnalysisAPIError) as dir_out:
            analyze_video_handler(
                "vid_001",
                request=AnalyzeVideoRequest(output_path="."),
                analyzer=self.analyzer,
            )
        self.assertEqual(dir_out.exception.status_code, 422)

        with self.assertRaises(AnalysisAPIError) as meta_bad_id:
            get_metadata_handler("..", analyzer=self.analyzer)
        self.assertEqual(meta_bad_id.exception.status_code, 422)

        fake_analyzer = MagicMock()
        fake_analyzer.config = self.config
        fake_analyzer.get_video_processed_dir.return_value = self.storage_dir / "processed" / "vid_001"
        fake_analyzer.analyze_video.side_effect = MediaExtractionError("corrupt segment")
        with self.assertRaises(AnalysisAPIError) as media_err:
            analyze_video_handler("vid_001", analyzer=fake_analyzer)
        self.assertEqual(media_err.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
