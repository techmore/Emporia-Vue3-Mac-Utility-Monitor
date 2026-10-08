import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import energy
import panel_photos
import web


class PanelPhotoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patch = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'energy.db'))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.directory.cleanup()

    def image(self):
        out = io.BytesIO()
        image = Image.new('RGB', (100, 60), 'olive')
        exif = Image.Exif()
        exif[270] = 'private panel location'
        image.save(out, format='JPEG', exif=exif)
        out.seek(0)
        return out

    def test_reencode_strips_metadata_and_keeps_private_permissions(self):
        name = panel_photos.save_photo(self.image())
        path = panel_photos.photo_path(name)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        with Image.open(path) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(dict(image.getexif()), {})
        self.assertEqual(panel_photos.list_photos(), [name])

    def test_invalid_data_and_path_traversal_are_rejected(self):
        with self.assertRaises(ValueError):
            panel_photos.save_photo(io.BytesIO(b'<svg>not a photo</svg>'))
        for name in ('../keys.json', '/tmp/photo.jpg', 'photo.jpg'):
            with self.assertRaises(ValueError):
                panel_photos.photo_path(name)
        self.assertEqual(panel_photos.list_photos(), [])

    def test_upload_count_is_bounded(self):
        for _ in range(6):
            panel_photos.save_photo(self.image())
        with self.assertRaises(ValueError):
            panel_photos.save_photo(self.image())
        self.assertEqual(len(panel_photos.list_photos()), 6)

    def test_upload_gallery_serve_delete_and_cross_origin_protection(self):
        energy.ensure_table()
        client = web.app.test_client()
        denied = client.post('/api/panel-photos', data={'photo': (self.image(), 'panel.jpg')},
                             headers={'Origin': 'https://untrusted.example'})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(panel_photos.list_photos(), [])
        response = client.post('/api/panel-photos', data={'photo': (self.image(), '../../keys.json')})
        self.assertEqual(response.status_code, 201)
        name = response.get_json()['photo']
        gallery = client.get('/panel/photos').get_data(as_text=True)
        self.assertIn('Settings Workspace', gallery)
        self.assertIn(name, gallery)
        asset = client.get('/api/panel-photos/' + name)
        self.assertEqual(asset.mimetype, 'image/jpeg')
        self.assertEqual(asset.headers['Cache-Control'], 'no-store')
        asset.close()
        self.assertEqual(client.delete('/api/panel-photos/' + name, json={}).status_code, 200)
        self.assertEqual(client.get('/api/panel-photos/' + name).status_code, 404)

    def test_byte_limit_and_symlink_rejection(self):
        with patch.object(panel_photos, 'MAX_BYTES', 3):
            with self.assertRaises(ValueError):
                panel_photos.save_photo(io.BytesIO(b'1234'))
        root = panel_photos.photo_directory()
        name = 'a' * 32 + '.jpg'
        (root / name).symlink_to(Path(self.directory.name) / 'keys.json')
        with self.assertRaises(ValueError):
            panel_photos.photo_path(name)
        self.assertEqual(panel_photos.list_photos(), [])

    def test_phone_orientation_is_applied_before_metadata_is_removed(self):
        image = Image.new('RGB', (100, 60), 'olive')
        exif = Image.Exif()
        exif[274] = 6
        payload = io.BytesIO()
        image.save(payload, format='JPEG', exif=exif)
        payload.seek(0)
        name = panel_photos.save_photo(payload)
        with Image.open(panel_photos.photo_path(name)) as saved:
            self.assertEqual(saved.size, (60, 100))
            self.assertEqual(dict(saved.getexif()), {})

    def test_pixel_limit_and_corrupt_image_write_nothing(self):
        with patch.object(panel_photos, 'MAX_PIXELS', 1):
            with self.assertRaises(ValueError):
                panel_photos.save_photo(self.image())
        payload = self.image().getvalue()
        with self.assertRaises(ValueError):
            panel_photos.save_photo(io.BytesIO(payload[:len(payload)//2]))
        self.assertEqual(panel_photos.list_photos(), [])

    def test_multipart_request_size_limit_and_missing_file(self):
        client = web.app.test_client()
        self.assertEqual(client.post('/api/panel-photos', json={}).status_code, 415)
        self.assertEqual(client.post('/api/panel-photos', data={},
                                    content_type='multipart/form-data').status_code, 400)
        with patch.object(panel_photos, 'MAX_BYTES', 1):
            response = client.post('/api/panel-photos',
                                   data={'photo': (io.BytesIO(b'x'*70000), 'large.jpg')})
            self.assertEqual(response.status_code, 413)
        self.assertEqual(panel_photos.list_photos(), [])

    def test_concurrent_uploads_cannot_exceed_photo_limit(self):
        panel_photos.photo_directory()
        def upload(_):
            try:
                return panel_photos.save_photo(self.image())
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(upload, range(8)))
        self.assertEqual(sum(name is not None for name in results), 6)
        self.assertEqual(len(panel_photos.list_photos()), 6)
