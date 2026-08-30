# Multilingual subtitles and translations

The extension lets each session choose a spoken language and a translation language in **Settings → General**. The choices are saved locally and sent with every WebSocket configuration message.

Supported language codes are: `nl`, `en`, `de`, `fr`, `es`, `it`, `pt`, `pl`, `tr`, `ru`, `uk`, `ar`, `hi`, `zh`, `ja`, `ko`, `sv`, `da`, `no`, and `fi`.

## Prepare the multilingual model

From the repository root:

```bash
make install-backend
bash backend/scripts/prepare_translation_ct2.sh
```

The preparation script downloads `facebook/m2m100_418M` and converts it to `backend/models/m2m100-418m-ct2`. The current defaults in `backend/.env.example` and `backend/run_gpu.sh` select this model automatically.

If an existing `backend/.env` still points to OPUS-MT, replace its translation settings with:

```dotenv
TRANSLATION_ENGINE=auto
TRANSLATION_MODEL_FAMILY=m2m100
TRANSLATION_MODEL=models/m2m100-418m-ct2
TRANSLATION_TOKENIZER=facebook/m2m100_418M
TRANSFORMERS_TRANSLATION_MODEL=facebook/m2m100_418M
```

Then restart the backend and reload the extension. An old Marian/OPUS model remains usable for Dutch→English, but the backend rejects other pairs with an actionable `unsupported_language_pair` error instead of returning output in the wrong language.

## API contract

The WebSocket configuration accepts `source_lang` and `target_lang`. Partial and final events include both fields and expose source speech under `source_text`. The legacy `dutch` field remains during migration so existing saved sessions and integrations continue to load.

`GET /api/languages` returns the user-facing catalog and, after model startup, the active translation model capabilities.
