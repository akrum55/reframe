"""Camera-local, recoverable photo-pair trash. No purge or cloud storage.

Only the hardware service writes this store, under its operation lock. A
durable per-photo journal precedes any moves. Link/unlink moves never overwrite
an existing destination and can be resumed after a process interruption.
The store and active folders must be on the same local filesystem.
"""

import json
import hashlib
import os
import re
import stat
import threading
import time
import uuid
from pathlib import Path


class TrashError(ValueError):
    pass


def validate_ids(values, entries=False):
    pattern = r"[a-f0-9]{32}" if entries else r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
    if not isinstance(values, list) or not 1 <= len(values) <= 100:
        raise TrashError("Select between 1 and 100 photos at a time.")
    if any(not isinstance(value, str) or not re.fullmatch(pattern, value) for value in values):
        raise TrashError("Invalid photo selection.")
    return list(dict.fromkeys(values))


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _regular(path):
    """Reject symlinks (including dangling links), devices, and directories."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise TrashError("Unsafe file type; no photo was overwritten.")
    return True


def _move(source, destination):
    source_exists, destination_exists = _regular(source), _regular(destination)
    if destination_exists:
        # A crash may leave both links to the SAME inode. Never accept a copy
        # merely because its name, size, or contents happen to match.
        if source_exists and not os.path.samefile(source, destination):
            raise TrashError("A file already exists at the destination; nothing was overwritten.")
    elif source_exists:
        with source.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(source, destination, follow_symlinks=False)
        _sync_dir(destination.parent)
    else:
        raise TrashError("A photo file is missing. Keep the Trash folder for recovery.")
    if source_exists:
        source.unlink()
        _sync_dir(source.parent)


def _fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition):
    if not condition:
        raise TrashError("Invalid Trash journal; preserve this folder for recovery.")


class PendingPhotoSaves:
    """Reserve before starting each worker, so overlapping saves stay busy."""

    def __init__(self):
        self._lock = threading.Lock()
        self._count = 0

    def busy(self):
        with self._lock:
            return self._count > 0

    def start(self, work):
        with self._lock:
            self._count += 1

        def run():
            try:
                work()
            finally:
                with self._lock:
                    self._count -= 1

        try:
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
        except BaseException:
            with self._lock:
                self._count -= 1
            raise
        return worker


class PhotoTrash:
    def __init__(self, originals, processed, root):
        self.originals, self.processed, self.root = map(Path, (originals, processed, root))
        self.lock = threading.RLock()
        for folder in (self.originals, self.processed, self.root):
            if folder.is_symlink():
                raise TrashError("Photo storage cannot be a symbolic link.")
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not folder.is_dir():
                raise TrashError("Photo storage is not a directory.")
        if len({p.stat().st_dev for p in (self.originals, self.processed, self.root)}) != 1:
            raise TrashError("Trash and photos must be on the same filesystem.")
        # Complete durable intents before the camera allocates another ID.
        for entry in self._entries():
            if entry["state"] in {"moving", "restoring"}:
                try:
                    self._finish(entry, restore=entry["state"] == "restoring")
                except (OSError, TrashError):
                    # Files stay recoverable; expose pending state in Trash.
                    pass

    @staticmethod
    def _allowed(photo_id, kind):
        bases = [photo_id] if kind == "original" else [photo_id, photo_id + "_dithered"]
        return {base + ext for base in bases for ext in (".png", ".jpg", ".jpeg")}

    def _entries(self):
        result = []
        for folder in self.root.iterdir():
            if not re.fullmatch(r"[a-f0-9]{32}", folder.name) or folder.is_symlink() or not folder.is_dir():
                raise TrashError("Unexpected Trash contents; preserve this folder for recovery.")
            manifest = folder / "manifest.json"
            if not _regular(manifest):
                if any(not (p.name == "manifest.tmp" and _regular(p)) and
                       not (p.name in {"original", "processed"} and not p.is_symlink()
                            and p.is_dir() and not any(p.iterdir())) for p in folder.iterdir()):
                    raise TrashError("Missing Trash journal; preserve this folder for recovery.")
                continue  # Interrupted before any photo could move.
            try:
                entry = json.loads(manifest.read_text())
                validate_ids([entry["photo_id"]])
                _require(entry["id"] == folder.name and entry["version"] == 1)
                _require(entry["state"] in {"moving", "trashed", "restoring", "restored"})
                _require(isinstance(entry["trashed_at"], (int, float)))
                _require(isinstance(entry["files"], list) and 1 <= len(entry["files"]) <= 9)
                seen = set()
                for item in entry["files"]:
                    _require(item["kind"] in {"original", "processed"})
                    _require(isinstance(item["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", item["sha256"]))
                    _require(isinstance(item["size"], int) and item["size"] >= 0)
                    name = Path(item["name"])
                    _require(name.name == item["name"] and name.stem + name.suffix.lower() in self._allowed(entry["photo_id"], item["kind"]))
                    _require((item["kind"], item["name"]) not in seen)
                    seen.add((item["kind"], item["name"]))
                _require(any(item["kind"] == "original" for item in entry["files"]))
            except (ValueError, KeyError, TypeError, AttributeError):
                raise TrashError("Invalid Trash journal; preserve this folder for recovery.") from None
            result.append(entry)
        return result

    def _journal(self, entry):
        folder = self.root / entry["id"]
        temporary = folder / "manifest.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(entry, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, folder / "manifest.json")
        _sync_dir(folder)

    def _paths(self, entry, item):
        active = self.originals if item["kind"] == "original" else self.processed
        saved = self.root / entry["id"] / item["kind"]
        if active.is_symlink() or self.root.is_symlink() or saved.parent.is_symlink() or saved.is_symlink() or not saved.is_dir():
            raise TrashError("Unsafe Trash folder; preserve it for recovery.")
        return active / item["name"], saved / item["name"]

    def _finish(self, entry, restore=False):
        # Preflight the WHOLE pair before moving any of it.
        if restore:
            original_names = {item["name"] for item in entry["files"] if item["kind"] == "original"}
            for path in self.originals.iterdir():
                if path.stem == entry["photo_id"] and path.suffix.lower() in {".png", ".jpg", ".jpeg"} and path.name not in original_names:
                    raise TrashError("Another active photo has this ID. Nothing was overwritten.")
        for item in entry["files"]:
            active, saved = self._paths(entry, item)
            source, destination = (saved, active) if restore else (active, saved)
            has_source, has_destination = _regular(source), _regular(destination)
            if has_source and (source.stat().st_size != item["size"] or _fingerprint(source) != item["sha256"]):
                raise TrashError("A photo file has been replaced. Nothing was moved.")
            if not has_source and not has_destination:
                raise TrashError("A photo file is missing. Keep the Trash folder for recovery.")
            if not has_source and has_destination and (destination.stat().st_size != item["size"] or _fingerprint(destination) != item["sha256"]):
                raise TrashError("An unrelated file occupies the destination. Nothing was overwritten.")
            if has_source and has_destination and not os.path.samefile(source, destination):
                raise TrashError("An active photo has the same filename. Nothing was overwritten.")
        for item in entry["files"]:
            active, saved = self._paths(entry, item)
            _move(saved, active) if restore else _move(active, saved)
        entry["state"] = "restored" if restore else "trashed"
        self._journal(entry)

    def highest_reserved_index(self):
        with self.lock:
            return max((int(e["photo_id"]) for e in self._entries() if e["photo_id"].isdigit()), default=-1)

    @staticmethod
    def _public(entry):
        return {"id": entry["id"], "photo_id": entry["photo_id"],
                "trashed_at": entry["trashed_at"], "state": entry["state"],
                "needs_attention": entry["state"] not in {"trashed", "restored"}}

    def list_entries(self):
        with self.lock:
            return sorted((self._public(e) for e in self._entries() if e["state"] != "restored"),
                          key=lambda e: e["trashed_at"], reverse=True)

    def move_to_trash(self, photo_ids):
        ids = validate_ids(photo_ids)
        completed, failed = [], []
        with self.lock:
            entries = self._entries()
            for photo_id in ids:
                entry = None
                try:
                    files = []
                    for kind, directory in (("original", self.originals), ("processed", self.processed)):
                        if directory.is_symlink():
                            raise TrashError("Photo storage cannot be a symbolic link.")
                        allowed = self._allowed(photo_id, kind)
                        for path in directory.iterdir():
                            # Photo IDs are case sensitive; suffixes are not.
                            candidate = path.stem + path.suffix.lower()
                            if candidate in allowed and _regular(path):
                                files.append({"kind": kind, "name": path.name, "size": path.stat().st_size,
                                              "sha256": _fingerprint(path)})
                    if not any(f["kind"] == "original" for f in files):
                        previous = next((e for e in reversed(entries) if e["photo_id"] == photo_id and e["state"] == "trashed"), None)
                        if previous:  # Retrying after a lost response is safe.
                            completed.append(self._public(previous))
                            continue
                        raise TrashError("Photo is no longer in the gallery. Refresh and try again.")
                    if len(files) > 9:
                        raise TrashError("Too many file variants for this photo; review before moving it.")
                    entry = {"version": 1, "id": uuid.uuid4().hex, "photo_id": photo_id,
                             "trashed_at": time.time(), "state": "moving", "files": files}
                    folder = self.root / entry["id"]
                    folder.mkdir(mode=0o700)
                    for kind in ("original", "processed"):
                        (folder / kind).mkdir(mode=0o700)
                    _sync_dir(folder)
                    _sync_dir(self.root)
                    self._journal(entry)
                    self._finish(entry)
                    entries.append(entry)
                    completed.append(self._public(entry))
                except (OSError, TrashError) as error:
                    if entry:
                        # Compensate a partial pair; if compensation also fails,
                        # the durable journal and both copies are retained.
                        try:
                            entry["state"] = "restoring"
                            self._journal(entry)
                            self._finish(entry, restore=True)
                        except (OSError, TrashError):
                            pass
                    failed.append({"id": photo_id, "error": str(error) if isinstance(error, TrashError)
                                   else "Storage error. Check Trash and available space before retrying."})
        return {"success": not failed, "completed": completed, "failed": failed}

    def restore(self, entry_ids):
        ids = validate_ids(entry_ids, entries=True)
        completed, failed = [], []
        with self.lock:
            entries = {e["id"]: e for e in self._entries()}
            for entry_id in ids:
                try:
                    entry = entries.get(entry_id)
                    if not entry:
                        raise TrashError("Trash entry not found. Refresh and try again.")
                    if entry["state"] != "restored":
                        entry["state"] = "restoring"
                        self._journal(entry)
                        self._finish(entry, restore=True)
                    completed.append(self._public(entry))
                except (OSError, TrashError) as error:
                    failed.append({"id": entry_id, "error": str(error) if isinstance(error, TrashError)
                                   else "Storage error. Your files remain in Trash for recovery."})
        return {"success": not failed, "completed": completed, "failed": failed}

    def preview(self, entry_id):
        validate_ids([entry_id], entries=True)
        with self.lock:
            entry = next((e for e in self._entries() if e["id"] == entry_id and e["state"] != "restored"), None)
            if not entry:
                raise TrashError("Trash entry not found.")
            for item in sorted(entry["files"], key=lambda f: f["kind"] != "processed"):
                active, saved = self._paths(entry, item)
                # Read under lock: restore cannot move this file halfway through.
                for path in (saved, active):
                    if _regular(path) and path.stat().st_size == item["size"] and _fingerprint(path) == item["sha256"]:
                        return path.read_bytes(), "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            raise TrashError("Preview unavailable. Files may need recovery.")
