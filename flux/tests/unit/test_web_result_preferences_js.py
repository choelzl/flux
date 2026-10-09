"""The browser saves project preferences without relying on browser storage."""

import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static/result_table.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def test_queued_saves_snapshot_choices_keep_owner_and_recover_from_errors(tmp_path):
    (tmp_path / "results.mjs").write_text(SOURCE.read_text().replace('"./ui.js"', '"./ui.mjs"'))
    (tmp_path / "ui.mjs").write_text("""
export const enc = encodeURIComponent;
export const h = () => { throw Error('DOM not expected'); };
export const toast = (...args) => globalThis.notices.push(args);
export const api = (...args) => globalThis.request(...args);
""")
    (tmp_path / "test.mjs").write_text("""
import assert from 'node:assert/strict';
import { resultPreferences, flushResultPreferences, mainMeasurements, relativeMeasurement } from './results.mjs';
Object.defineProperty(globalThis, 'localStorage', {get() {throw Error('Browser storage must not be used');}});
globalThis.notices = [];
const requests = [], persisted = {hiddenMetrics: ['old']};
let fail = false, release;
const blocked = new Promise(resolve => {release = resolve;});
globalThis.request = async (path, options) => {
  requests.push({path, options});
  await blocked;
  if (options) {
    if (fail) throw Error('test refusal');
    Object.assign(persisted, options.body);
  }
  return {preferences: structuredClone(persisted)};
};
const info = {owner: 'original owner', result_preferences: structuredClone(persisted)};
const ctx = {name: 'project', info, mine: true};
const first = resultPreferences(ctx), second = resultPreferences(ctx);
const choices = {timings: {selected: 'timings.fast', expanded: false}};
first.save({dictionaryMetrics: choices});
choices.timings.selected = 'mutated';
second.save({mainMetrics: ['time'], relativeMetrics: {time: true}});
assert.equal(first.read().dictionaryMetrics.timings.selected, 'timings.fast');
assert.deepEqual(mainMeasurements(ctx, ['time', 'area']), ['time']);
assert.equal(relativeMeasurement(ctx, 'time'), true);
// Navigating to somebody else's project must not change the request's destination.
ctx.owner = 'new page owner';
release();
await flushResultPreferences();
assert.equal(requests.length, 2);
assert.ok(requests.every(r => r.path === '/apps/project/preferences?owner=original%20owner'));
assert.equal(requests[0].options.body.dictionaryMetrics.timings.selected, 'timings.fast');
assert.deepEqual(first.read(), persisted);
assert.deepEqual(persisted.hiddenMetrics, ['old']);
// A read-only viewer or preview can explore without persisting to this project.
resultPreferences({name: 'other', info: {owner: 'alice'}, mine: false}).save({graphs: {x: 'power'}});
resultPreferences({name: 'preview', mine: true}).save({graphs: {x: 'area'}});
await flushResultPreferences();
assert.equal(requests.length, 2);
// Failed saves are reported, restore the server's settings and do not poison the queue.
fail = true;
first.save({hiddenMetrics: ['failed']});
second.save({hiddenMetrics: ['also failed']});
await flushResultPreferences();
assert.equal(notices.length, 2);
assert.ok(notices.every(n => n[0].includes('were not saved: test refusal')));
assert.deepEqual(first.read().hiddenMetrics, ['old']);
fail = false;
await first.save({hiddenMetrics: []});
assert.deepEqual(persisted.hiddenMetrics, []);
assert.deepEqual(first.read(), persisted);
""")
    result = subprocess.run(["node", str(tmp_path / "test.mjs")], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
