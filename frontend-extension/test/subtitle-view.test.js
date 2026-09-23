const test = require("node:test");
const assert = require("node:assert/strict");

const { SubtitleView } = require("../app/subtitle-view.js");

function viewHarness() {
  const view = Object.create(SubtitleView.prototype);
  view.currentId = null;
  view.currentItem = null;
  view.historyRows = new Map();
  view.historyOrder = [];
  view.maxVisibleHistory = 80;
  view.announcement = { textContent: "" };
  view.prepended = [];
  view.idleRenders = 0;
  view.prependHistory = item => view.prepended.push(item);
  view.renderIdleLiveRow = () => { view.idleRenders += 1; };
  return view;
}

test("a completed live sentence moves immediately into history", () => {
  const view = viewHarness();
  const item = { id: "sentence-1", dutch: "Dit is klaar.", translation: "This is finished." };
  view.currentId = item.id;
  view.currentItem = { ...item, pending: true };

  view.showFinal(item);

  assert.deepEqual(view.prepended, [item]);
  assert.equal(view.currentId, null);
  assert.equal(view.currentItem, null);
  assert.equal(view.idleRenders, 1);
  assert.equal(view.announcement.textContent, item.translation);
});

test("restored completed sentences stay in history and leave the live row ready", () => {
  const view = viewHarness();
  view.clear = () => {};
  const items = [
    { id: "sentence-1", dutch: "Eerste." },
    { id: "sentence-2", dutch: "Tweede." }
  ];

  view.restore(items);

  assert.deepEqual(view.prepended, items);
  assert.equal(view.currentId, null);
  assert.equal(view.currentItem, null);
  assert.equal(view.idleRenders, 1);
});

test("history DOM is bounded to maxVisibleHistory", () => {
  const removed = [];
  const view = Object.create(SubtitleView.prototype);
  view.liveRow = { parentNode: null };
  view.feed = {
    insertBefore() {},
    children: []
  };
  view.historyRows = new Map();
  view.historyOrder = [];
  view.maxVisibleHistory = 3;
  view.createRow = () => ({
    dataset: {},
    remove() { removed.push(this); }
  });
  view.renderRow = () => {};
  view._trimVisibleHistory = SubtitleView.prototype._trimVisibleHistory;
  view.prependHistory = SubtitleView.prototype.prependHistory;
  view.promoteHistoryRow = SubtitleView.prototype.promoteHistoryRow;

  for (let i = 0; i < 5; i += 1) {
    view.prependHistory({ id: `id-${i}`, dutch: `zin ${i}` });
  }

  assert.equal(view.historyRows.size, 3);
  assert.deepEqual(view.historyOrder, ["id-4", "id-3", "id-2"]);
  assert.equal(removed.length, 2);
});
