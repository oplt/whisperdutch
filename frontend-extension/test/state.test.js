const test = require("node:test");
const assert = require("node:assert/strict");
const { AppState, TRANSITIONS } = require("../app/state.js");

test("AppState rejects unknown initial state", () => {
  assert.throws(() => new AppState("unknown"), /Unknown application state/);
});

test("AppState allows documented transitions", () => {
  const state = new AppState();
  assert.equal(state.value, "idle");
  state.begin("starting-backend", "Starting");
  state.transition("connecting", "Connecting");
  state.transition("capturing", "Listening");
  state.transition("paused", "Paused");
  state.transition("capturing", "Listening");
  state.begin("stopping", "Stopping");
  state.transition("idle", "Ready");
});

test("AppState rejects impossible transitions", () => {
  const state = new AppState();
  assert.throws(() => state.transition("capturing"), /Invalid application transition/);
});

test("generation ownership blocks stale async work", () => {
  const state = new AppState();
  const first = state.begin("starting-backend", "Starting");
  const second = state.begin("stopping", "Stopping");
  assert.equal(state.owns(first), false);
  assert.equal(state.owns(second), true);
  state.invalidate();
  assert.equal(state.owns(second), false);
});

test("error state can retry into starting-backend", () => {
  const state = new AppState();
  state.begin("starting-backend");
  state.transition("error", "Backend unavailable");
  const generation = state.retry();
  assert.equal(state.value, "starting-backend");
  assert.equal(state.owns(generation), true);
});

test("every state declares at least one outgoing transition", () => {
  Object.keys(TRANSITIONS).forEach(name => {
    assert.ok(TRANSITIONS[name].length >= 1, `${name} has no outgoing transitions`);
  });
});
