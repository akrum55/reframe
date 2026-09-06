/* Austin's selection, Trash, restore, and confirmed-empty UI. No image data or selection is persisted in
 * browser storage. Reload-safe recovery lives in the camera's Trash journal. */
(function (root) {
    'use strict';
    const escapeHtml = value => String(value).replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[ch]);
    const storageLabel = data => {
        const total = data.total_bytes, free = data.available_bytes;
        if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(free) || free < 0 || free > total)
            return 'SD storage: unavailable';
        const format = bytes => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${(bytes / 1e6).toFixed(1)} MB`;
        return `SD storage: ${format(free)} available / ${format(total)} total`;
    };

    class PhotoSelection {
        constructor() { this.ids = new Set(); }
        toggle(id) {
            if (this.ids.has(id)) this.ids.delete(id);
            else if (this.ids.size < 100) this.ids.add(id);
        }
        addPage(ids) { ids.forEach(id => { if (this.ids.size < 100) this.ids.add(id); }); }
        clear() { this.ids.clear(); }
    }

    class PhotoLibrary {
        constructor(options) {
            this.options = options;
            this.selection = new PhotoSelection();
            this.selecting = false;
            this.trashView = false;
            this.busy = false;
            this.trashPhotos = [];
            this.undoIds = [];
            this.loadVersion = 0;
            this.message = '';
            this.toolbar = document.getElementById('library-toolbar');
            this.notice = document.getElementById('library-notice');
            this.toolbar.addEventListener('click', event => {
                const button = event.target.closest('button[data-library-action]');
                if (button && !this.busy) this.action(button.dataset.libraryAction);
            });
            this.notice.addEventListener('click', event => {
                if (event.target.closest('[data-library-undo]') && !this.busy) this.restore(this.undoIds, true);
            });
            this.renderToolbar();
        }

        visiblePhotos() { return this.trashView ? this.trashPhotos : this.options.photos(); }

        async action(action) {
            this.options.activity();
            if (action === 'trash' || action === 'gallery') {
                this.loadVersion++;
                this.options.cancelLoads();
                this.trashView = action === 'trash';
                this.selecting = this.trashView;
                this.selection.clear();
                if (this.trashView) {
                    this.undoIds = [];
                    this.setNotice('');
                }
                this.renderToolbar();
                if (this.trashView) await this.loadTrash();
                else await this.options.reload();
            } else if (action === 'select' || action === 'cancel') {
                this.selecting = action === 'select';
                this.selection.clear();
                this.options.render();
                this.renderToolbar();
            } else if (action === 'page') {
                this.selection.addPage(this.visiblePhotos().map(photo => photo.id));
                this.render(this.options.grid());
            } else if (action === 'clear') {
                this.selection.clear();
                this.render(this.options.grid());
            } else if (action === 'move') {
                const count = this.selection.ids.size;
                if (count && confirm(`Move ${count} photo${count === 1 ? '' : 's'} to Trash? Originals and processed copies stay together. You can restore them later.`)) {
                    await this.mutate('/api/photos/trash', [...this.selection.ids], false);
                }
            } else if (action === 'restore') {
                await this.restore([...this.selection.ids]);
            } else if (action === 'empty') {
                await this.emptyTrash();
            }
        }

        async loadTrash() {
            const version = ++this.loadVersion;
            this.options.hidePagination();
            this.options.grid().innerHTML = '<div class="library-empty">loading trash...</div>';
            try {
                const response = await fetch('/api/trash', {cache: 'no-store'});
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Could not load Trash.');
                if (!this.trashView || version !== this.loadVersion) return;
                this.trashPhotos = data.photos || [];
                const existing = new Set(this.trashPhotos.map(photo => photo.id));
                this.selection.ids.forEach(id => { if (!existing.has(id)) this.selection.ids.delete(id); });
                this.render(this.options.grid());
            } catch (error) {
                if (!this.trashView || version !== this.loadVersion) return;
                this.options.grid().innerHTML = '<div class="library-empty">Trash is unavailable. Use refresh to try again.</div>';
                this.setNotice(error.message);
            }
        }

        renderToolbar() {
            const n = this.selection.ids.size;
            const button = (action, label, disabled = false) => `<button type="button" class="button" data-library-action="${action}" ${this.busy || disabled ? 'disabled' : ''}>${label}</button>`;
            this.toolbar.innerHTML = this.trashView
                ? button('gallery', 'back to photos') + '<strong>trash</strong>'
                : button(this.selecting ? 'cancel' : 'select', this.selecting ? 'cancel' : 'select photos') + button('trash', 'trash');
            if (this.selecting) {
                this.toolbar.innerHTML += `<span class="library-count" aria-live="polite">${n} selected</span>`
                    + button('page', this.trashView ? 'select all (up to 100)' : 'select this page', !this.visiblePhotos().length)
                    + button('clear', 'clear selection', !n)
                    + button(this.trashView ? 'restore' : 'move', this.trashView ? 'restore selected' : 'move to trash', !n);
            }
            if (this.trashView) this.toolbar.innerHTML += button('empty', 'empty trash', !this.trashPhotos.length)
                + '<p class="library-help">Restore photos to keep them, or empty Trash to permanently delete them. Nothing is automatically deleted. Trash still uses camera storage.</p>';
            else if (this.selecting) this.toolbar.innerHTML += '<p class="library-help">Tap photos to select them. Selections stay selected across pages (up to 100).</p>';
        }

        render(grid) {
            this.renderToolbar();
            if (!this.selecting && !this.trashView) return false;
            const visible = this.visiblePhotos();
            if (!visible.length) {
                grid.innerHTML = `<div class="library-empty">${this.trashView ? 'Trash is empty. Photos you move here can be restored later.' : 'No photos here. Cancel selection to return to the gallery.'}</div>`;
                return true;
            }
            grid.innerHTML = visible.map(photo => {
                const selected = this.selection.ids.has(photo.id);
                const label = `Photo ${this.trashView ? photo.photo_id : photo.id}`;
                const src = this.trashView ? `/api/trash/${encodeURIComponent(photo.id)}/preview` : photo.dithered_path || photo.original_path;
                const date = this.trashView ? new Date(photo.trashed_at * 1000).toLocaleString() : '';
                return `<button type="button" class="photo-choice" role="checkbox" aria-checked="${selected}" aria-label="${escapeHtml(label)}" data-photo-choice="${escapeHtml(photo.id)}" ${this.busy ? 'disabled' : ''}>
                    <img src="${escapeHtml(src)}" alt="" loading="lazy">
                    <span class="photo-check" aria-hidden="true">${selected ? '✓' : ''}</span>
                    <span class="photo-choice-caption">${escapeHtml(label)}${date ? `<br>trashed ${escapeHtml(date)}` : ''}${photo.needs_attention ? (photo.state === 'purging' ? '<br>deletion interrupted — empty Trash to finish; cannot restore' : '<br>interrupted operation — try restore') : ''}</span>
                </button>`;
            }).join('');
            grid.querySelectorAll('[data-photo-choice]').forEach(button => {
                button.addEventListener('click', () => {
                    if (this.busy) return;
                    const id = button.dataset.photoChoice;
                    if (!this.selection.ids.has(id) && this.selection.ids.size === 100) {
                        this.setNotice('Select up to 100 photos at a time. Move or restore this group first.');
                        return;
                    }
                    this.selection.toggle(id);
                    const checked = this.selection.ids.has(id);
                    button.setAttribute('aria-checked', String(checked));
                    button.querySelector('.photo-check').textContent = checked ? '✓' : '';
                    this.renderToolbar();
                    this.options.activity();
                });
            });
            return true;
        }

        setNotice(message) {
            this.message = message;
            this.notice.hidden = !message && !this.undoIds.length;
            this.notice.innerHTML = `<span>${escapeHtml(message)}</span>` + (this.undoIds.length
                ? `<button type="button" class="button" data-library-undo ${this.busy ? 'disabled' : ''}>undo last move</button>` : '');
        }

        async restore(ids, undo = false) {
            if (ids.length) await this.mutate('/api/trash/restore', [...ids], true, undo);
        }

        async emptyTrash() {
            if (this.busy || !this.trashView) return;
            this.busy = true;
            this.render(this.options.grid());
            this.setNotice('Checking Trash...');
            const headers = {'Content-Type': 'application/json', 'X-Reframe-Action': 'photo-library'};
            try {
                const prepared = await fetch('/api/trash/empty/preview', {method: 'POST', headers, body: '{}'});
                const plan = await prepared.json();
                if (!prepared.ok) throw new Error(plan.detail || 'Could not check Trash.');
                if (!plan.total) { this.setNotice('Trash is already empty.'); return; }
                if (!confirm(`Permanently delete all ${plan.total} photo${plan.total === 1 ? '' : 's'} in Trash? Both originals and processed copies will be deleted from the camera. This cannot be undone. Photos in your main gallery will not be deleted.`)) {
                    this.setNotice('');
                    return;
                }
                this.setNotice('Emptying Trash...');
                const response = await fetch('/api/trash/empty', {method: 'POST', headers,
                    body: JSON.stringify({snapshot: plan.snapshot, confirmation: 'empty-trash-permanently'})});
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || 'Could not empty Trash.');
                this.undoIds = [];
                this.selection.clear();
                this.setNotice(`${result.completed.length} photo${result.completed.length === 1 ? '' : 's'} permanently deleted.`
                    + (result.failed.length ? ` ${result.failed.length} could not be fully deleted: ${result.failed[0].error}` : ' Trash is empty.'));
            } catch (error) {
                this.setNotice(`${error.message} If interrupted, check Trash before retrying. Deletions already completed cannot be undone.`);
            } finally {
                this.busy = false;
                await this.loadTrash();
                if (this.options.updateStorage) await this.options.updateStorage();
                this.renderToolbar();
                this.setNotice(this.message);
            }
        }

        async mutate(url, ids, restoring, undo = false) {
            if (this.busy || !ids.length) return;
            this.busy = true;
            this.renderToolbar();
            this.render(this.options.grid());
            this.setNotice(restoring ? 'Restoring photos...' : 'Moving photos to Trash...');
            try {
                const response = await fetch(url, {method: 'POST', headers: {
                    'Content-Type': 'application/json', 'X-Reframe-Action': 'photo-library'
                }, body: JSON.stringify({ids})});
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || 'The camera could not complete that action.');
                const completed = result.completed || [];
                const failed = result.failed || [];
                completed.forEach(photo => this.selection.ids.delete(restoring ? photo.id : photo.photo_id));
                if (!restoring && completed.length) this.undoIds = completed.map(photo => photo.id);
                if (restoring) this.undoIds = this.undoIds.filter(id => !completed.some(photo => photo.id === id));
                this.setNotice(`${completed.length} photo${completed.length === 1 ? '' : 's'} ${restoring ? 'restored' : 'moved to Trash'}.`
                    + (failed.length ? ` ${failed.length} could not be ${restoring ? 'restored' : 'moved'}: ${failed[0].error} Successful photos are already updated.` : ''));
                if (!this.trashView && !failed.length) {
                    this.selecting = false;
                    this.selection.clear();
                }
                await this.options.updateCount();
            } catch (error) {
                this.setNotice(`${error.message} If the connection was interrupted, check Trash before retrying; completed moves remain recoverable.`);
            } finally {
                this.busy = false;
                if (this.trashView) await this.loadTrash();
                else await this.options.reload();
                if (this.options.updateStorage) await this.options.updateStorage();
                this.renderToolbar();
                this.setNotice(this.message);
            }
        }
    }

    root.PhotoLibrary = PhotoLibrary;
    root.storageLabel = storageLabel;
    if (typeof module !== 'undefined') module.exports = {PhotoSelection, escapeHtml, PhotoLibrary, storageLabel};
})(typeof window !== 'undefined' ? window : globalThis);
