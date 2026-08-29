const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const subtitleHtml = fs.readFileSync(
  path.join(__dirname, "..", "subtitle.html"),
  "utf8"
);
const uiComponents = fs.readFileSync(
  path.join(__dirname, "..", "ui-components.js"),
  "utf8"
);
const subtitleScript = fs.readFileSync(
  path.join(__dirname, "..", "subtitle.js"),
  "utf8"
);
const workletScript = fs.readFileSync(
  path.join(__dirname, "..", "worklet.js"),
  "utf8"
);

test("completed subtitles are merged into the current subtitle panel", () => {
  const currentPanelStart = subtitleHtml.indexOf('class="panel current-panel"');
  const historyList = subtitleHtml.indexOf('id="historySubtitles"');

  assert.notEqual(currentPanelStart, -1);
  assert.ok(historyList > currentPanelStart);
  assert.doesNotMatch(subtitleHtml, /Previous subtitles/);
  assert.doesNotMatch(subtitleHtml, /history-inline|historyDrawer/);
  assert.doesNotMatch(subtitleHtml, /fullscreenBtn/);
});

test("subtitle feed has captured and English columns without metadata containers", () => {
  assert.match(subtitleHtml, /<h3>Captured subtitles<\/h3>/);
  assert.match(subtitleHtml, /<h3>English translation<\/h3>/);
  assert.match(subtitleHtml, /class="subtitle-feed"[^>]*newest first/);
  assert.doesNotMatch(uiComponents, /subtitle-meta|formatClock|createQualityPill/);
  assert.match(subtitleScript, /historySubtitlesEl\.prepend\(historyRow\)/);
  assert.match(subtitleScript, /subtitleFeedEl\.scrollTop = 0/);
});

test("live window contains backend status, lifecycle controls, and diagnostics", () => {
  assert.match(subtitleHtml, /id="backendToolbar"/);
  assert.match(subtitleHtml, /id="restartBackendBtn"/);
  assert.match(subtitleHtml, /id="stopBackendBtn"/);
  assert.match(subtitleHtml, /id="backendDiagnostics"/);
  assert.match(subtitleScript, /if \(autoStartRequested\) \{[\s\S]*?await startTranslation\(\)/);
  assert.match(subtitleScript, /command: restart \? "restart_backend" : "start_backend"/);
});

test("all supported transcript export formats remain reachable", () => {
  assert.match(subtitleHtml, /id="exportTxtBtn"/);
  assert.match(subtitleHtml, /id="exportVttBtn"/);
  assert.match(subtitleHtml, /id="exportSrtBtn"/);
  assert.match(subtitleScript, /exportTranscript\("txt"\)/);
  assert.match(subtitleScript, /exportTranscript\("vtt"\)/);
  assert.match(subtitleScript, /exportTranscript\("srt"\)/);
});

test("capture lifecycle flushes finals and reconnects without retaining stale sockets", () => {
  assert.match(subtitleScript, /waitForBackendFlush\(8000\)/);
  assert.match(subtitleScript, /payload\.type === "flushed"/);
  assert.match(subtitleScript, /clearTimeout\(reconnectTimer\)/);
  assert.doesNotMatch(subtitleScript, /function scheduleReconnect\(socket\)/);
  assert.match(subtitleScript, /safeCleanup\(\(\) => socket\.close\(\)\)/);
});

test("audio worklet converts directly to PCM and reports level off the UI thread", () => {
  assert.match(workletScript, /resampleToPCM16LE/);
  assert.match(workletScript, /this\.port\.postMessage\(\{ pcm, level \}/);
  assert.doesNotMatch(workletScript, /this\.sourceBuffer = new Float32Array\(this\.sourceBufferSize\);[\s\S]*this\.sourceBuffer = new Float32Array/);
  assert.doesNotMatch(subtitleScript, /function readPcmLevel/);
});
