"""Storage 업로드→작업 등록→조회와 OpenAI 입력 가림. 외부 서비스/GPU 호출 없음."""

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.deps import get_user_id
from app.main import app
from app.routes import photos, segmentation
from app.services import db, images, photo_source, storage
from app.worker import queue
from scripts import local_gpu


def landmarks():
    points = [{"index": i, "x": 0.5, "y": 0.4, "z": 0, "visibility": 0.99} for i in range(33)]
    for i in range(11):
        points[i].update(x=0.45 + (i % 5) * 0.025, y=0.12 + (i // 5) * 0.02)
    points[11].update(x=0.35, y=0.3)
    points[12].update(x=0.65, y=0.3)
    points[23].update(x=0.42, y=0.65)
    points[24].update(x=0.58, y=0.65)
    return points


def photo_bytes(size=(600, 400)):
    return images.encode_photo(Image.new("RGB", size, (210, 40, 35)))[0]


class StoredSourceTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(settings, "storage_face_mask_style", "fill").start()
        self.raw = photo_bytes((300, 400))
        self.download = patch.object(storage, "download", return_value=self.raw).start()
        self.photo = {
            "session_id": str(uuid4()),
            "kind": "USER",
            "storage_bucket": "photos",
            "storage_path": "test/session/user.jpg",
            "pose_landmarks": landmarks(),
        }
        self.addCleanup(photo_source.clear, self.photo["session_id"])

    def test_segmentation_reads_unchanged_bytes_vlm_gets_masked_copy(self):
        self.assertEqual(photo_source.load(self.photo), self.raw)
        masked = photo_source.load(self.photo, for_vlm=True)
        self.assertNotEqual(masked, self.raw)
        image = images.load_rgb(masked)
        self.assertLess(max(abs(v - 128) for v in image.getpixel((150, 55))), 5)
        self.assertGreater(image.getpixel((150, 300))[0], 190)
        self.assertEqual(self.photo["pose_landmarks"], landmarks())
        self.assertEqual(photo_source.load(self.photo), self.raw)

    def test_memory_pod_copies_still_take_precedence(self):
        photo_source.put(self.photo["session_id"], "USER", b"seg", vlm=b"masked")
        self.assertEqual(photo_source.load(self.photo), b"seg")
        self.assertEqual(photo_source.load(self.photo, for_vlm=True), b"masked")
        self.download.assert_not_called()

    def test_old_photo_without_landmarks_is_not_sent_unmasked(self):
        self.photo["pose_landmarks"] = None
        self.assertEqual(photo_source.load(self.photo), self.raw)
        with self.assertRaises(photo_source.PhotoPrivacyUnavailable):
            photo_source.load(self.photo, for_vlm=True)

    def test_missing_fileless_photo_does_not_attempt_storage(self):
        self.photo["storage_path"] = None
        with self.assertRaises(photo_source.PhotoUnavailable):
            photo_source.load(self.photo)
        self.download.assert_not_called()


class StoredPhotoApiTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(settings, "photo_pipeline", "storage").start()
        patch.object(settings, "storage_face_mask_style", "fill").start()
        patch.object(settings, "pose_threshold", 70).start()
        patch.object(
            db, "get_client", side_effect=AssertionError("Unexpected cloud access")
        ).start()
        self.user_id, self.session_id = uuid4(), uuid4()
        self.rows, self.files, self.jobs = {}, {}, []
        self.previous_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_user_id] = lambda: self.user_id
        self.addCleanup(self.restore_overrides)
        patch.object(
            db,
            "get_session",
            return_value={
                "session_id": str(self.session_id),
                "user_id": str(self.user_id),
            },
        ).start()
        patch.object(
            db, "get_photo", side_effect=lambda sid, kind: self.rows.get(str(kind))
        ).start()
        patch.object(db, "get_segmentation", return_value=None).start()
        patch.object(db, "create_photo", side_effect=self.create_photo).start()
        patch.object(db, "delete_photo").start()
        self.discard = patch.object(photos, "_discard_existing").start()
        self.upload = patch.object(storage, "upload", side_effect=self.save_file).start()
        patch.object(
            storage, "download", side_effect=lambda bucket, path: self.files[(bucket, path)]
        ).start()
        patch.object(
            storage,
            "signed_url",
            side_effect=lambda bucket, path: (
                "https://storage.example/" + path,
                datetime.now(timezone.utc) + timedelta(minutes=10),
            ),
        ).start()
        self.remove = patch.object(storage, "remove").start()
        self.enqueue = patch.object(queue, "enqueue", side_effect=self.enqueue_job).start()
        patch.object(
            queue,
            "list_jobs",
            side_effect=lambda sid, kind: [j for j in self.jobs if j["kind"] == kind],
        ).start()
        self.screen = patch.object(photos.photo_screening, "screen", new_callable=AsyncMock).start()
        self.screen.return_value = SimpleNamespace(suitable=True)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.base = f"/api/v1/sessions/{self.session_id}/photos"

    def restore_overrides(self):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.previous_overrides)

    def create_photo(self, row):
        result = {
            **copy.deepcopy(row),
            "photo_id": str(uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.rows[result["kind"]] = result
        return result

    def save_file(self, bucket, path, data, content_type):
        self.files[(bucket, path)] = data

    def enqueue_job(self, sid, kind, payload):
        job = {"job_id": str(uuid4()), "kind": kind, "payload": payload}
        self.jobs.append(job)
        return job

    def upload_photo(self, kind="reference", include_file=True, **overrides):
        data = {
            "pose_landmarks": json.dumps(landmarks()),
            "pose_scale_basis": "TORSO",
            "pipeline": "full",
        }
        if kind == "user":
            data.update(capture_source="UPLOAD", pose_similarity="90", framing_score="0.95")
        data.update(overrides)
        kwargs = (
            {"files": {"file": ("test.jpg", photo_bytes(), "image/jpeg")}} if include_file else {}
        )
        return self.client.post(f"{self.base}/{kind}", data=data, **kwargs)

    def test_reference_upload_and_reload_share_stored_coordinate_frame(self):
        response = self.upload_photo()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual((body["width"], body["height"]), (300, 400))
        self.assertEqual(body["pose_landmarks"], self.rows["REFERENCE"]["pose_landmarks"])
        self.assertNotEqual(body["pose_landmarks"][11]["x"], landmarks()[11]["x"])
        self.assertIn(str(self.user_id), self.rows["REFERENCE"]["storage_path"])
        self.assertEqual(str(self.jobs[0]["kind"]), "SEG_REFERENCE")
        self.assertEqual(self.jobs[0]["payload"]["photo_id"], body["photo_id"])
        response = self.client.get(f"{self.base}/reference")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pose_landmarks"], body["pose_landmarks"])
        self.assertTrue(response.json()["signed_url"])
        self.assertTrue(response.json()["signed_url_expires_at"])

    def test_user_upload_screens_masked_pair_and_stores_unmasked_analysis_photo(self):
        self.assertEqual(self.upload_photo().status_code, 201)
        response = self.upload_photo("user")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["pose_similarity"], 90)
        self.assertEqual(response.json()["framing_score"], 0.95)
        self.assertEqual(str(self.jobs[-1]["kind"]), "SEG_USER")
        screened_user, screened_ref = self.screen.await_args.args
        self.assertEqual(screened_user.getpixel((300, 55)), (128, 128, 128))
        self.assertLess(
            max(abs(v - 128) for v in images.load_rgb(screened_ref).getpixel((150, 55))), 5
        )
        stored = photo_source.load(self.rows["USER"])
        self.assertGreater(images.load_rgb(stored).getpixel((150, 55))[0], 190)
        self.assertEqual(photo_source.held(), 0)
        response = self.client.get(f"{self.base}/user")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["signed_url"])

    def test_mirrored_screening_uses_matching_unmirrored_landmarks(self):
        self.assertEqual(self.upload_photo().status_code, 201)
        with patch.object(photo_source, "mask_for_vlm", wraps=photo_source.mask_for_vlm) as mask:
            response = self.upload_photo("user", is_mirrored="true")
        self.assertEqual(response.status_code, 201, response.text)
        user_points = mask.call_args.args[1]
        self.assertAlmostEqual(user_points[0]["x"], 1 - landmarks()[0]["x"])
        self.assertTrue(self.rows["USER"]["was_mirrored"])

    def test_file_is_required_before_any_storage_write(self):
        response = self.upload_photo(include_file=False)
        self.assertEqual(response.status_code, 400)
        self.upload.assert_not_called()
        self.enqueue.assert_not_called()

    def test_existing_pose_threshold_still_rejects_before_screening_or_replacement(self):
        self.assertEqual(self.upload_photo().status_code, 201)
        self.upload.reset_mock()
        self.discard.reset_mock()
        response = self.upload_photo("user", pose_similarity="1")
        self.assertEqual(response.status_code, 422, response.text)
        self.screen.assert_not_awaited()
        self.upload.assert_not_called()
        self.discard.assert_not_called()

    def test_failed_job_registration_removes_new_storage_file(self):
        self.enqueue.side_effect = RuntimeError("synthetic queue failure")
        with self.assertLogs("app", level="ERROR"), self.assertRaises(RuntimeError):
            self.upload_photo()
        self.remove.assert_called_once()
        self.assertEqual(self.remove.call_args.args[1], [self.rows["REFERENCE"]["storage_path"]])

    def test_missing_reference_blob_never_bypasses_screening(self):
        self.assertEqual(self.upload_photo().status_code, 201)
        self.files.clear()
        self.upload.reset_mock()
        with self.assertLogs("routes.photos", level="ERROR"):
            response = self.upload_photo("user")
        self.assertEqual(response.status_code, 503)
        self.screen.assert_not_awaited()
        self.upload.assert_not_called()

    def test_old_reference_without_landmarks_requires_reupload(self):
        self.assertEqual(self.upload_photo().status_code, 201)
        self.rows["REFERENCE"]["pose_landmarks"] = None
        response = self.upload_photo("user")
        self.assertEqual(response.status_code, 409)
        self.screen.assert_not_awaited()

    def test_swagger_distinguishes_validation_and_pose_rejection(self):
        schema = self.client.get("/openapi.json").json()
        responses = schema["paths"]["/api/v1/sessions/{session_id}/photos/user"]["post"][
            "responses"
        ]
        for status in ("400", "409", "422", "503"):
            self.assertIn(status, responses)
        self.assertEqual(
            responses["422"]["content"]["application/json"]["schema"],
            {
                "$ref": "#/components/schemas/ErrorResponse",
            },
        )

    def test_saved_photo_and_maps_can_be_retrieved_together(self):
        self.assertEqual(self.upload_photo().status_code, 201)
        result = {
            "segmentation_id": str(uuid4()),
            "storage_bucket": "segmentations",
            "map_path": "user/session/reference/map.png",
            "map_width": 768,
            "map_height": 1024,
            "model_name": "sapiens2",
            "model_version": "sapiens2-seg-1b",
            "person_area_ratio": 0.5,
        }
        with (
            patch.object(db, "get_segmentation", return_value=result),
            patch.object(db, "list_part_segments", return_value=[]),
        ):
            view = segmentation._build(self.rows["REFERENCE"], master={})
        self.assertTrue(view.photo_url)
        self.assertTrue(view.map_url.endswith("map.png"))
        self.assertTrue(view.merged_map_url.endswith("map_merged.png"))
        self.assertEqual((view.photo_width, view.photo_height), (300, 400))
        self.assertEqual((view.map_width, view.map_height), (768, 1024))


class LocalGpuLauncherTests(unittest.TestCase):
    def test_check_does_not_import_or_start_worker(self):
        with patch.object(local_gpu, "check_gpu", return_value={"ok": True}) as check:
            self.assertEqual(local_gpu.main(["check", "--repeat", "1"]), 0)
        check.assert_called_once_with(1)

    def test_pod_config_cannot_consume_storage_jobs(self):
        with (
            patch.object(settings, "photo_pipeline", "pod"),
            patch.object(local_gpu, "check_gpu") as check,
        ):
            self.assertEqual(local_gpu.main(["run"]), 1)
        check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
