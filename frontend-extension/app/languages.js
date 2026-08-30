(function (root) {
  const LANGUAGE_OPTIONS = Object.freeze([
    ["nl", "Dutch"],
    ["en", "English"],
    ["de", "German"],
    ["fr", "French"],
    ["es", "Spanish"],
    ["it", "Italian"],
    ["pt", "Portuguese"],
    ["pl", "Polish"],
    ["tr", "Turkish"],
    ["ru", "Russian"],
    ["uk", "Ukrainian"],
    ["ar", "Arabic"],
    ["hi", "Hindi"],
    ["zh", "Chinese"],
    ["ja", "Japanese"],
    ["ko", "Korean"],
    ["sv", "Swedish"],
    ["da", "Danish"],
    ["no", "Norwegian"],
    ["fi", "Finnish"]
  ]);
  const LANGUAGE_CODES = new Set(LANGUAGE_OPTIONS.map(([code]) => code));

  function normalizeLanguage(value, fallback) {
    const code = String(value || "").trim().toLowerCase();
    return LANGUAGE_CODES.has(code) ? code : fallback;
  }

  function languageName(code) {
    return LANGUAGE_OPTIONS.find(([candidate]) => candidate === code)?.[1] || String(code || "").toUpperCase();
  }

  function populateLanguageSelect(select, documentRef) {
    if (!select || select.options?.length) return;
    LANGUAGE_OPTIONS.forEach(([code, name]) => {
      const option = documentRef.createElement("option");
      option.value = code;
      option.textContent = name;
      select.appendChild(option);
    });
  }

  function allowedTargetLanguages(sourceLang, capabilities) {
    const source = normalizeLanguage(sourceLang, "nl");
    if (!capabilities || capabilities.multilingual) {
      return LANGUAGE_OPTIONS.map(([code]) => code).filter(code => code !== source);
    }
    const pairs = Array.isArray(capabilities.supported_pairs) ? capabilities.supported_pairs : [];
    const targets = pairs.filter(([src]) => src === source).map(([, tgt]) => tgt);
    return targets.length ? targets : ["en"];
  }

  function applyTranslationCapabilities(sourceSelect, targetSelect, capabilities, documentRef = root.document) {
    if (!sourceSelect || !targetSelect) return { sourceLang: "nl", targetLang: "en" };
    const sourceLang = normalizeLanguage(sourceSelect.value, "nl");
    const allowedTargets = new Set(allowedTargetLanguages(sourceLang, capabilities));
    const previousTarget = normalizeLanguage(targetSelect.value, "en");
    targetSelect.replaceChildren();
    LANGUAGE_OPTIONS.forEach(([code, name]) => {
      if (!allowedTargets.has(code)) return;
      const option = documentRef.createElement("option");
      option.value = code;
      option.textContent = name;
      targetSelect.appendChild(option);
    });
    const targetLang = allowedTargets.has(previousTarget)
      ? previousTarget
      : [...allowedTargets][0] || "en";
    targetSelect.value = targetLang;
    return { sourceLang, targetLang };
  }

  function translationCapabilityHint(capabilities) {
    if (!capabilities) return "";
    if (capabilities.multilingual) {
      return "This translation model supports all listed language pairs.";
    }
    const pairs = Array.isArray(capabilities.supported_pairs) ? capabilities.supported_pairs : [];
    if (!pairs.length) return "";
    const labels = pairs.map(([source, target]) => `${languageName(source)} → ${languageName(target)}`);
    return `Current translation model supports: ${labels.join(", ")}. Prepare an NLLB or M2M100 model for more pairs.`;
  }

  const api = {
    LANGUAGE_OPTIONS,
    LANGUAGE_CODES,
    normalizeLanguage,
    languageName,
    populateLanguageSelect,
    allowedTargetLanguages,
    applyTranslationCapabilities,
    translationCapabilityHint
  };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
