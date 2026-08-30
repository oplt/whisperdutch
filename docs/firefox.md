# Firefox installation

The Firefox build uses the same local transcription and translation backend as the Chromium build. Firefox does not provide Chromium's per-tab audio capture API, so on Linux the extension captures a PipeWire or PulseAudio **monitor source** instead.

## Requirements

- Firefox 140 or newer
- Linux with PipeWire or PulseAudio
- A visible system-output monitor source
- The backend dependencies and models described by the project setup

List the available audio sources:

```bash
pactl list short sources
```

Look for a source whose name ends in `.monitor` or whose description starts with `Monitor of`. Install and open `pavucontrol` if Firefox does not show that source directly.

## Build and install

From the repository root:

```bash
npm run build:firefox
bash native-host/install_linux.sh
```

Do not run the native-host installer with `sudo`; it installs a per-user manifest at `~/.mozilla/native-messaging-hosts/`. Restart Firefox after running it.

Load the development build:

1. Open `about:debugging#/runtime/this-firefox`.
2. Select **Load Temporary Add-on**.
3. Select `dist/firefox/manifest.json`.

A temporary add-on is removed when Firefox restarts. Build again and use **Reload** on the debugging page after changing extension files. Normal Firefox releases require Mozilla signing for permanent installation.

The Firefox manifest declares website-content processing because captured audio and generated subtitles leave the extension for the local backend. Processing stays on the same computer; the extension does not send audio or subtitles to a remote service.

## Start subtitles

1. Start the Dutch video or audio in Firefox.
2. Click the extension toolbar button.
3. In the subtitle window, click **Start listening**.
4. In Firefox's audio permission dialog, select the system-output monitor source—not the microphone.
5. Keep the source playing while the local models start.

Firefox leaves the original audio playback under Firefox and system volume control. The extension's tab-volume and mute controls are disabled in this mode to prevent duplicated playback.

## Troubleshooting

### The monitor source is missing from Firefox

Allow the initial audio request, open `pavucontrol`, switch to **Recording**, and change the Firefox recording stream to **Monitor of …**. Then click **Retry** in the subtitle window if needed.

### Native messaging host not found

Run the installer again without `sudo`, confirm this file exists, and restart Firefox:

```text
~/.mozilla/native-messaging-hosts/com.polatozgur111.dutch_subtitle_backend.json
```

Ubuntu's confined Firefox package uses the WebExtensions XDG desktop portal for native messaging and may show a one-time authorization prompt. Accept that prompt so Firefox can start the local backend.

### Listening works but no subtitles appear

- Confirm the input level moves in the subtitle window.
- Confirm the selected source is a monitor, not a physical microphone.
- Open **Settings → Advanced → Backend diagnostics** and check that the backend and models are ready.
- Inspect the local logs under `backend/logs/`.

## Platform limitation

Firefox currently cannot isolate one tab's output audio through a WebExtension API. The monitor source captures system output, so sounds from other applications can also be transcribed. Use Chromium or Brave when strict per-tab capture is required.
