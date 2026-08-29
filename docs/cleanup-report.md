# Cleanup report

Recorded on 2026-08-29 after Tasks 13–40.

## Deleted files

| File | Reason |
| --- | --- |
| `frontend-extension/subtitle.js` | Replaced by modular `frontend-extension/app/*` controller |
| `frontend-extension/settings.js` | Settings persistence moved into `app/settings-view.js` |
| `frontend-extension/ui-components.js` | Badge/card helpers belonged to removed dashboard UI |
| `frontend-extension/worklet.js` | Moved to `frontend-extension/audio/worklet.js` with streaming resampler |
| `frontend-extension/test/settings.test.js` | Tested removed `settings.js` module |
| `frontend-extension/test/subtitle-layout.test.js` | Asserted removed dashboard layout and badge markup |

## Deleted functions / classes

| Symbol | Former location | Reason |
| --- | --- | --- |
| `SubtitleUI.createSubtitleCard` | `ui-components.js` | History rows now built incrementally in `SubtitleView` |
| `SubtitleUI.setBadgeState` | `ui-components.js` | Multiple status badges removed |
| `SubtitleUI.setBadgeValue` | `ui-components.js` | No badge value elements remain |
| `Settings.getDisplayMode` | `settings.js` | Single simplified subtitle view; no display-mode switch |
| `Settings.setMonitor` / `getMonitor` | `settings.js` | Inlined into `SettingsView` |
| Monolithic capture/WebSocket helpers | `subtitle.js` | Split into `CaptureController`, `SubtitleSocket`, `BackendService`, `main.js` |

## Deleted CSS selectors / UI sections

| Area | Reason |
| --- | --- |
| `.status-badge`, dashboard metric cards | Task 19 minimal status dot |
| Target-language selector markup/styles | English-only target is hardcoded |
| Audio-source selector markup/styles | Current-tab capture only |
| Speaker diarization / confidence controls | Unsupported backend features removed |
| Visible Reconnect button | Automatic reconnect + error Retry only |
| Old tabbed dashboard shell | Replaced by subtitle-first layout and native `<dialog>` settings |

## Deleted dependencies

| Dependency group | Change |
| --- | --- |
| Runtime `pytest`, `ruff`, `mypy` | Moved to `backend/requirements-dev.txt` |

## Retained on purpose

| File | Reason |
| --- | --- |
| `Procfile` | Used by `Makefile local-dev` and documented backend launch |
| `PRODUCT.md` | Product notes outside runtime path |
| `subtitle-renderer.js` | Shared partial stabilization + export formatting |
