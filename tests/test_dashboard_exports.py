import asyncio
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fastapi.responses import FileResponse, Response

import dashboard


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._data


class FakeArenaClient:
    def __init__(self):
        self.upload_content = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        if url.endswith("/uploads/presign"):
            return FakeResponse({
                "files": [{
                    "upload_url": "https://upload.invalid/photo",
                    "key": "photo.png",
                    "content_type": "image/png"
                }]
            })
        return FakeResponse({"id": 123, "url": "https://www.are.na/block/123"})

    async def put(self, url, content, headers):
        self.upload_content = content
        return FakeResponse({})


class DitheredExportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.image_path = Path(self.temp_dir.name) / "test_dithered.png"
        image = Image.new("RGB", (2, 2))
        image.putdata([
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 255),
        ])
        image.save(self.image_path, format="PNG")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_disabled_export_returns_original_png_bytes(self):
        content, filename, media_type = dashboard.prepare_dithered_export(
            str(self.image_path),
            False
        )

        self.assertEqual(content, self.image_path.read_bytes())
        self.assertEqual(filename, self.image_path.name)
        self.assertEqual(media_type, "image/png")

    def test_enabled_export_doubles_dimensions_without_cropping(self):
        content, filename, media_type = dashboard.prepare_dithered_export(
            str(self.image_path),
            True
        )

        with Image.open(BytesIO(content)) as image:
            self.assertEqual(image.size, (4, 4))
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(image.getpixel((3, 0)), (0, 255, 0))
            self.assertEqual(image.getpixel((0, 3)), (0, 0, 255))
            self.assertEqual(image.getpixel((3, 3)), (255, 255, 255))

        self.assertEqual(filename, self.image_path.name)
        self.assertEqual(media_type, "image/png")

    def test_individual_download_only_upscales_when_enabled(self):
        with patch.object(dashboard, "DITHERED_PHOTOS_PATH", self.temp_dir.name):
            with patch.object(
                dashboard.settings_manager,
                "load_settings",
                return_value={"exports": {"upscale_dithered_2x": False}}
            ):
                response = asyncio.run(
                    dashboard.download_dithered_photo(self.image_path.name)
                )
                self.assertIsInstance(response, FileResponse)

            with patch.object(
                dashboard.settings_manager,
                "load_settings",
                return_value={"exports": {"upscale_dithered_2x": True}}
            ):
                response = asyncio.run(
                    dashboard.download_dithered_photo(self.image_path.name)
                )
                self.assertIsInstance(response, Response)
                with Image.open(BytesIO(response.body)) as image:
                    self.assertEqual(image.size, (4, 4))

    def test_arena_upload_receives_upscaled_png(self):
        fake_client = FakeArenaClient()
        settings = {
            "exports": {"upscale_dithered_2x": True},
            "extensions": {
                "arena": {
                    "enabled": True,
                    "channel": "test-channel",
                    "access_token": "test-token"
                }
            },
            "system": {}
        }
        photo = {
            "id": "test",
            "dithered_path": str(self.image_path),
            "created": "2026-07-20T12:00:00"
        }

        with patch.object(dashboard.httpx, "AsyncClient", return_value=fake_client):
            result = asyncio.run(dashboard.ArenaExtension().run(photo, settings))

        self.assertEqual(result["status"], "success")
        self.assertIsNotNone(fake_client.upload_content)
        with Image.open(BytesIO(fake_client.upload_content)) as image:
            self.assertEqual(image.size, (4, 4))


if __name__ == "__main__":
    unittest.main()
