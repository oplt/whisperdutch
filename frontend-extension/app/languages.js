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

  const api = { LANGUAGE_OPTIONS, LANGUAGE_CODES, normalizeLanguage, languageName, populateLanguageSelect };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
