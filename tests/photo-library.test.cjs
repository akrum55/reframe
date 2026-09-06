const {test, afterEach} = require('node:test');
const assert = require('node:assert/strict');
const {PhotoSelection, PhotoLibrary, escapeHtml} = require('../static/photo-library.js');

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
afterEach(() => { global.fetch = originalFetch; });
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
