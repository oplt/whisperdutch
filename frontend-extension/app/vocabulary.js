(function (root) {
  const STORAGE_KEY = "practisingVocabulary";

  class VocabularyStore {
    constructor(storage = root.localStorage) {
      this.storage = storage;
    }

    readAll() {
      try {
        const parsed = JSON.parse(this.storage?.getItem(STORAGE_KEY) || "[]");
        return Array.isArray(parsed) ? parsed : [];
      } catch (_error) {
        return [];
      }
    }

    writeAll(entries) {
      this.storage?.setItem(STORAGE_KEY, JSON.stringify(entries));
    }

    normalizeWord(word) {
      return String(word || "").trim().toLowerCase();
    }

    findDuplicate(entries, word, dutchSentence, sourceLanguage = "nl") {
      const normalizedWord = this.normalizeWord(word);
      const normalizedSentence = String(dutchSentence || "").trim();
      return entries.find(entry =>
        this.normalizeWord(entry.word) === normalizedWord
        && String(entry.dutchSentence || "").trim() === normalizedSentence
        && String(entry.sourceLanguage || "nl") === sourceLanguage
      );
    }

    add(entry) {
      const entries = this.readAll();
      const duplicate = this.findDuplicate(entries, entry.word, entry.dutchSentence, entry.sourceLanguage || "nl");
      if (duplicate) {
        duplicate.meaning = entry.meaning || duplicate.meaning;
        duplicate.englishSentence = entry.englishSentence || duplicate.englishSentence;
        duplicate.sourceLanguage = entry.sourceLanguage || duplicate.sourceLanguage || "nl";
        duplicate.targetLanguage = entry.targetLanguage || duplicate.targetLanguage || "en";
        duplicate.addedAt = entry.addedAt || duplicate.addedAt;
        this.writeAll(entries);
        return { entry: duplicate, created: false };
      }
      entries.unshift(entry);
      this.writeAll(entries);
      return { entry, created: true };
    }

    remove(id) {
      const entries = this.readAll().filter(entry => entry.id !== id);
      this.writeAll(entries);
      return entries;
    }

    clear() {
      this.writeAll([]);
    }
  }

  class VocabularyController {
    constructor(documentRef = root.document, storage = root.localStorage) {
      this.document = documentRef;
      this.store = new VocabularyStore(storage);
      const byId = id => documentRef.getElementById(id);
      this.listDialog = byId("vocabularyDialog");
      this.openButton = byId("vocabularyBtn");
      this.listEl = byId("vocabularyList");
      this.emptyEl = byId("vocabularyEmpty");
      this.sourceLanguage = "nl";
      this.targetLanguage = "en";

      this.openButton?.addEventListener("click", () => this.openList());
      byId("clearVocabularyBtn")?.addEventListener("click", () => this.clearAll());
    }

    openList() {
      this.renderList();
      if (typeof this.listDialog.showModal === "function") this.listDialog.showModal();
      else this.listDialog.setAttribute("open", "");
    }

    addFromSubtitle(payload) {
      const word = String(payload?.word || "").trim();
      if (!word) return null;
      const dutchSentence = String(payload?.dutchSentence || "").trim();
      const englishSentence = String(payload?.englishSentence || "").trim();
      const meaning = englishSentence || "Translation pending";
      return this.store.add({
        id: `vocab-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        word,
        meaning,
        dutchSentence,
        englishSentence: englishSentence || meaning,
        sourceLanguage: payload.sourceLanguage || this.sourceLanguage,
        targetLanguage: payload.targetLanguage || this.targetLanguage,
        addedAt: new Date().toISOString()
      });
    }

    setLanguages(sourceLanguage, targetLanguage) {
      this.sourceLanguage = sourceLanguage || "nl";
      this.targetLanguage = targetLanguage || "en";
      if (this.emptyEl) {
        const name = root.SubtitleApp?.languageName?.(this.sourceLanguage) || this.sourceLanguage.toUpperCase();
        this.emptyEl.textContent = `Click a ${name} word in the subtitles to add it here.`;
      }
    }

    clearAll() {
      if (!this.store.readAll().length) return;
      if (!root.confirm("Clear all practising vocabulary entries?")) return;
      this.store.clear();
      this.renderList();
    }

    renderList() {
      const entries = this.store.readAll();
      this.listEl.replaceChildren();
      this.emptyEl.hidden = entries.length > 0;
      entries.forEach(entry => {
        const row = this.document.createElement("article");
        row.className = "vocabulary-entry";
        row.dataset.entryId = entry.id;

        const heading = this.document.createElement("div");
        heading.className = "vocabulary-entry-heading";
        const title = this.document.createElement("h3");
        title.className = "vocabulary-entry-word";
        title.lang = entry.sourceLanguage || "nl";
        title.dir = "auto";
        title.textContent = entry.word;
        heading.append(title);

        const dutch = this.document.createElement("p");
        dutch.className = "vocabulary-entry-sentence";
        dutch.lang = entry.sourceLanguage || "nl";
        dutch.dir = "auto";
        dutch.textContent = entry.dutchSentence || "—";

        const meaning = this.document.createElement("p");
        meaning.className = "vocabulary-entry-meaning";
        meaning.lang = entry.targetLanguage || "en";
        meaning.dir = "auto";
        meaning.textContent = entry.meaning;

        const remove = this.document.createElement("button");
        remove.type = "button";
        remove.className = "text-button vocabulary-remove";
        remove.textContent = "Remove";
        remove.addEventListener("click", () => {
          this.store.remove(entry.id);
          this.renderList();
        });

        row.append(heading, dutch, meaning, remove);
        this.listEl.appendChild(row);
      });
    }
  }

  const api = { VocabularyStore, VocabularyController, STORAGE_KEY };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
