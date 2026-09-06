"""Small hardware API adapter; no camera imports, also usable in fixture tests."""

from fastapi import HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from photo_trash import TrashError, validate_ids


def register_photo_trash_routes(app, get_store, operation_lock, is_busy, touch):
    @app.get("/api/storage")
    def storage_status():
        try:
            return get_store().storage_status()
        except OSError:
            raise HTTPException(503, 'Storage information is unavailable.') from None

    @app.get("/api/trash")
    def list_trash():
        try:
            entries = get_store().list_entries()
            return {"photos": entries, "total": len(entries)}
        except TrashError as error:
            raise HTTPException(409, str(error)) from None

    @app.get("/api/trash/{entry_id}/preview")
    def trash_preview(entry_id: str):
        try:
            data, media_type = get_store().preview(entry_id)
            return Response(data, media_type=media_type, headers={"Cache-Control": "no-store"})
        except TrashError as error:
            raise HTTPException(404, str(error)) from None

    async def mutate(request, restore):
        try:
            body = await request.json()
            ids = validate_ids(body.get("ids") if isinstance(body, dict) else None, entries=restore)
        except (ValueError, TrashError) as error:
            raise HTTPException(400, str(error) if isinstance(error, TrashError) else "Invalid selection.") from None
        return await run_in_threadpool(mutate_locked, ids, restore)

    def mutate_locked(ids, restore):
        if not operation_lock.acquire(blocking=False):
            raise HTTPException(409, "Camera is busy. Wait for it to finish, then try again.")
        try:
            store = get_store()
            if is_busy():
                raise HTTPException(409, "Camera is saving or refreshing. Wait, then try again.")
            touch()
            return store.restore(ids) if restore else store.move_to_trash(ids)
        except TrashError as error:
            raise HTTPException(409, str(error)) from None
        finally:
            operation_lock.release()

    @app.post("/api/photos/trash")
    async def move_photos(request: Request):
        return await mutate(request, restore=False)

    @app.post("/api/trash/restore")
    async def restore_photos(request: Request):
        return await mutate(request, restore=True)

    def empty_locked(snapshot=None):
        if not operation_lock.acquire(blocking=False):
            raise HTTPException(409, 'Camera is busy. Wait, then try again.')
        try:
            store = get_store()
            if is_busy():
                raise HTTPException(409, 'Camera is saving or refreshing. Wait, then try again.')
            touch()
            return store.empty_preview() if snapshot is None else store.empty(snapshot)
        except TrashError as error:
            raise HTTPException(409, str(error)) from None
        except OSError:
            raise HTTPException(503, 'Storage could not be checked. No further deletion was attempted.') from None
        finally:
            operation_lock.release()

    @app.post('/api/trash/empty/preview')
    async def preview_empty():
        return await run_in_threadpool(empty_locked)

    @app.post('/api/trash/empty')
    async def empty_trash(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or body.get('confirmation') != 'empty-trash-permanently' or not isinstance(body.get('snapshot'), str):
                raise ValueError()
        except ValueError:
            raise HTTPException(400, 'Explicit confirmation and a current Trash snapshot are required.') from None
        return await run_in_threadpool(empty_locked, body['snapshot'])
