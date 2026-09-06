const {test, afterEach} = require('node:test');
const assert = require('node:assert/strict');
const {PhotoSelection, PhotoLibrary, escapeHtml, storageLabel} = require('../static/photo-library.js');

test('individual selection toggles without adding duplicate IDs', () => {
    const selection = new PhotoSelection();
    selection.toggle('00001');
    assert.deepEqual([...selection.ids], ['00001']);
    selection.toggle('00001');
    assert.equal(selection.ids.size, 0);
});
test('select page adds to selections on other pages and caps at 100', () => {
    const selection = new PhotoSelection();
    selection.addPage(['00001', '00002']);
    selection.addPage(['00002', '00003']);
    assert.deepEqual([...selection.ids], ['00001', '00002', '00003']);
    selection.addPage(Array.from({length: 200}, (_, i) => String(i + 100)));
    assert.equal(selection.ids.size, 100);
    selection.clear();
    assert.equal(selection.ids.size, 0);
});
test('photo labels and server errors are escaped before rendering', () => {
    assert.equal(escapeHtml('<img src="x" onerror=\'bad\'>&'), '&lt;img src=&quot;x&quot; onerror=&#39;bad&#39;&gt;&amp;');
});

const originalFetch = global.fetch;
const originalConfirm = global.confirm;
afterEach(() => { global.fetch = originalFetch; global.confirm = originalConfirm; });
function library() {
    const instance = Object.create(PhotoLibrary.prototype);
    Object.assign(instance, {selection: new PhotoSelection(), undoIds: [], selecting: true,
        trashView: false, busy: false, message: '', renderToolbar() {}, render() {},
        setNotice(message) { this.message = message; },
        options: {grid() {}, updateCount: async () => {}, reload: async () => {}}
    });
    return instance;
}
test('move uses only selected IDs, clears successful IDs, preserves partial failures for retry', async () => {
    const instance = library();
    instance.selection.addPage(['00001', '00002']);
    global.fetch = async (url, options) => {
        assert.equal(url, '/api/photos/trash');
        assert.deepEqual(JSON.parse(options.body), {ids: ['00001', '00002']});
        assert.equal(options.headers['X-Reframe-Action'], 'photo-library');
        return {ok: true, json: async () => ({completed: [{id: 'entry1', photo_id: '00001'}],
            failed: [{id: '00002', error: 'fixture error'}]})};
    };
    await instance.mutate('/api/photos/trash', [...instance.selection.ids], false);
    assert.deepEqual([...instance.selection.ids], ['00002']);
    assert.deepEqual(instance.undoIds, ['entry1']);
    assert.match(instance.message, /1 photo moved to Trash.*1 could not/);
    assert.equal(instance.busy, false);
});

test('opening Trash dismisses moved message and Undo permanently for this visit', async () => {
    const instance = library();
    Object.assign(instance, {undoIds: ['old-entry'], message: '10 photos moved to Trash.', loadVersion: 0,
        loadTrash: async () => {}});
    Object.assign(instance.options, {activity() {}, cancelLoads() {}});
    await instance.action('trash');
    assert.equal(instance.message, '');
    assert.deepEqual(instance.undoIds, []);
    await instance.action('gallery');
    assert.equal(instance.message, '');
    assert.deepEqual(instance.undoIds, []);
});

function trashLibrary() {
    const instance = library();
    Object.assign(instance, {trashView: true, trashPhotos: [{id: 'entry'}], loadTrash: async () => {}});
    return instance;
}

test('Empty Trash cancellation never submits permanent deletion', async () => {
    const instance = trashLibrary();
    let requests = 0;
    global.fetch = async url => {
        assert.equal(url, '/api/trash/empty/preview'); requests++;
        return {ok: true, json: async () => ({total: 10, snapshot: 'reviewed'})};
    };
    global.confirm = message => {
        assert.match(message, /all 10 photos/);
        assert.match(message, /cannot be undone/);
        assert.match(message, /main gallery will not be deleted/);
        return false;
    };
    await instance.emptyTrash();
    assert.equal(requests, 1);
    assert.equal(instance.busy, false);
    assert.equal(instance.message, '');
});

test('Empty Trash submits only the reviewed snapshot and refreshes storage', async () => {
    const instance = trashLibrary();
    instance.undoIds = ['entry'];
    let requests = 0, storageRefreshes = 0;
    instance.options.updateStorage = async () => { storageRefreshes++; };
    global.confirm = () => true;
    global.fetch = async (url, options) => {
        assert.equal(options.headers['X-Reframe-Action'], 'photo-library');
        if (requests++ === 0) return {ok: true, json: async () => ({total: 1, snapshot: 'reviewed'})};
        assert.equal(url, '/api/trash/empty');
        assert.deepEqual(JSON.parse(options.body), {snapshot: 'reviewed', confirmation: 'empty-trash-permanently'});
        return {ok: true, json: async () => ({completed: [{id: 'entry'}], failed: []})};
    };
    await instance.emptyTrash();
    assert.equal(requests, 2);
    assert.equal(storageRefreshes, 1);
    assert.deepEqual(instance.undoIds, []);
    assert.match(instance.message, /1 photo permanently deleted/);
});

test('Empty Trash stale snapshot or connection loss never silently retries', async () => {
    for (const interrupted of [false, true]) {
        const instance = trashLibrary();
        let requests = 0;
        global.confirm = () => true;
        global.fetch = async () => {
            if (requests++ === 0) return {ok: true, json: async () => ({total: 1, snapshot: 'reviewed'})};
            if (interrupted) throw new Error('Lost connection');
            return {ok: false, json: async () => ({detail: 'Trash changed. Review again.'})};
        };
        await instance.emptyTrash();
        assert.equal(requests, 2);
        assert.match(instance.message, /cannot be undone/);
        assert.equal(instance.busy, false);
    }
});

test('Empty Trash zero and duplicate submits never delete', async () => {
    const instance = trashLibrary();
    let requests = 0;
    global.fetch = async () => { requests++; return {ok: true, json: async () => ({total: 0, snapshot: 'empty'})}; };
    global.confirm = () => { throw new Error('Nothing to confirm'); };
    instance.busy = true;
    await instance.emptyTrash();
    assert.equal(requests, 0);
    instance.busy = false;
    await instance.emptyTrash();
    assert.equal(requests, 1);
    assert.match(instance.message, /already empty/);
});

test('storage readout formats capacity/free bytes and rejects unavailable data', () => {
    assert.equal(storageLabel({total_bytes: 32e9, available_bytes: 24.1e9}), 'SD storage: 24.1 GB available / 32.0 GB total');
    assert.equal(storageLabel({total_bytes: 32e9, available_bytes: 0}), 'SD storage: 0.0 MB available / 32.0 GB total');
    for (const data of [{}, {total_bytes: 1, available_bytes: 2}, {total_bytes: 1, available_bytes: -1}])
        assert.equal(storageLabel(data), 'SD storage: unavailable');
});
test('Undo restores entry IDs rather than current gallery selection', async () => {
    const instance = library();
    instance.undoIds = ['entry1', 'entry2'];
    instance.selection.addPage(['unrelated']);
    global.fetch = async (url, options) => {
        assert.equal(url, '/api/trash/restore');
        assert.deepEqual(JSON.parse(options.body).ids, ['entry1', 'entry2']);
        return {ok: true, json: async () => ({completed: [{id: 'entry1'}, {id: 'entry2'}], failed: []})};
    };
    await instance.restore(instance.undoIds, true);
    assert.deepEqual(instance.undoIds, []);
    assert.equal(instance.selecting, false);
});
test('busy response preserves selection and does not pretend it succeeded', async () => {
    const instance = library();
    instance.selection.addPage(['00001']);
    global.fetch = async () => ({ok: false, json: async () => ({detail: 'Camera is saving'})});
    await instance.mutate('/api/photos/trash', ['00001'], false);
    assert.deepEqual([...instance.selection.ids], ['00001']);
    assert.match(instance.message, /Camera is saving/);
    assert.deepEqual(instance.undoIds, []);
    assert.equal(instance.busy, false);
});
test('lost connection gives recovery guidance and double-submit is suppressed', async () => {
    const instance = library();
    instance.busy = true;
    global.fetch = async () => { throw new Error('Should not submit twice'); };
    await instance.mutate('/api/photos/trash', ['00001'], false);
    assert.equal(instance.message, '');
    instance.busy = false;
    global.fetch = async () => { throw new Error('Connection lost'); };
    await instance.mutate('/api/photos/trash', ['00001'], false);
    assert.match(instance.message, /check Trash before retrying/);
    assert.equal(instance.busy, false);
});
