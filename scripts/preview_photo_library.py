#!/usr/bin/env python3
"""Local preview with disposable synthetic photos; never connects to a camera.

Run from the repository: .venv/bin/python scripts/preview_photo_library.py
Only listens on loopback. The fixture directory is removed on normal exit.
"""
import argparse
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image, ImageDraw
import uvicorn

import dashboard
from photo_trash import PhotoTrash
from photo_trash_api import register_photo_trash_routes


def make_preview(base, port):
    originals, processed = base / 'photos', base / 'dithered_photos'
    store = PhotoTrash(originals, processed, base / '.photo-trash')
    colors = ['#dc8b57', '#9aaa86', '#839bb6', '#d9b35e', '#a48dab', '#8da6a0']
    for index in range(16):
        image = Image.new('RGB', (600, 400), colors[index % len(colors)])
        draw = ImageDraw.Draw(image)
        draw.ellipse((360 - index * 4, 35, 455 - index * 4, 130), fill='#f5f1f0')
        draw.polygon([(0, 320), (160, 130 + index * 5), (300, 285), (470, 165), (600, 300), (600, 400), (0, 400)], fill='#3d4a49')
        draw.rectangle((20, 345, 350, 380), fill='#f5f1f0')
        draw.text((30, 356), f'SAMPLE {index + 1:02d} / DISPOSABLE TEST IMAGE', fill='#181818')
        image.save(originals / f'{index:05d}.jpg', quality=90)
        image.save(processed / f'{index:05d}_dithered.png')
    dashboard.PHOTOS_PATH = str(originals)
    dashboard.DITHERED_PHOTOS_PATH = str(processed)
    dashboard.SETTINGS_PATH = str(base / 'settings.json')
    dashboard.settings_manager = dashboard.SettingsManager(dashboard.SETTINGS_PATH)
    dashboard.photo_manager = dashboard.PhotoManager()
    hardware = FastAPI()
    register_photo_trash_routes(hardware, lambda: store, threading.Lock(), lambda: False, lambda: None)

    @hardware.get('/api/photos')
    def list_photos():
        photos = dashboard.photo_manager.get_all_photos(limit=10000)['photos']
        for photo in photos:
            photo['original_path'] = str(originals / photo['filename'])
            photo['dithered_path'] = str(processed / (photo['id'] + '_dithered.png'))
        return photos

    @hardware.post('/api/timeout/reset')
    def reset():
        return {'success': True}

    app = dashboard.app
    app.mount('/preview-hardware', hardware)
    dashboard.reframe_client = dashboard.ReframeClient(f'http://127.0.0.1:{port}/preview-hardware/api')

    @app.middleware('http')
    async def isolate_preview(request, call_next):
        path = request.url.path
        if path == '/':
            response = await dashboard.dashboard()
            html = response.body.decode().replace('<body>', '<body><p style="padding:16px;border-bottom:1px dotted;text-align:center">LOCAL PREVIEW · disposable sample images · no camera connection</p>')
            html = html.replace('onclick="capturePhoto()"', 'disabled title="Camera controls disabled in preview"')
            return HTMLResponse(html)
        if path == '/api/battery':
            return JSONResponse({'battery_level': None, 'source': 'preview'})
        if path == '/api/extensions/actions':
            return JSONResponse({'actions': []})
        if path == '/api/timeout/status':
            return JSONResponse({'enabled': False})
        if path == '/api/settings' and request.method == 'GET':
            return JSONResponse(dashboard.settings_manager.load_settings())
        allowed_get = path.startswith(('/assets/', '/photos/', '/dithered/', '/api/trash', '/preview-hardware/')) or path == '/api/photos'
        allowed_post = path in {'/api/photos/trash', '/api/trash/restore', '/api/timeout/reset',
                               '/preview-hardware/api/photos/trash', '/preview-hardware/api/trash/restore',
                               '/preview-hardware/api/timeout/reset'}
        if (request.method == 'GET' and allowed_get) or (request.method == 'POST' and allowed_post):
            return await call_next(request)
        return JSONResponse({'detail': 'This is a photo-library preview. Camera and settings operations are disabled.'}, status_code=503)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='reframe-photo-preview-') as folder:
        preview = make_preview(Path(folder), args.port)
        uvicorn.run(preview, host='127.0.0.1', port=args.port, log_level='warning')
