(function (root) {
  class GlossaryController {
    constructor(client = root.BackendClient, documentRef = root.document) {
      this.client = client;
      this.document = documentRef;
      const byId = id => documentRef.getElementById(id);
      this.dialog = byId("glossaryDialog");
      this.openButton = byId("openGlossaryBtn");
      this.search = byId("glossarySearch");
      this.pattern = byId("glossaryPattern");
      this.replacement = byId("glossaryReplacement");
      this.list = byId("glossaryList");
      this.rules = [];
      this.openButton.addEventListener("click", () => this.open());
      this.search.addEventListener("input", () => this.render());
      byId("addGlossaryBtn").addEventListener("click", () => this.add());
    }

    async load() {
      const data = await this.client.fetchGlossary();
      this.rules = data.rules || [];
      this.render();
    }

    async open() {
      await this.load().catch(() => {});
      if (typeof this.dialog.showModal === "function") this.dialog.showModal();
      else this.dialog.setAttribute("open", "");
      this.search.focus();
    }

    async add() {
      const pattern = this.pattern.value.trim();
      if (!pattern) return;
      this.rules.push({ pattern, replacement: this.replacement.value.trim() });
      this.pattern.value = "";
      this.replacement.value = "";
      await this.save();
    }

    async remove(index) {
      this.rules.splice(index, 1);
      await this.save();
    }

    async save() {
      const data = await this.client.saveGlossaryRules(this.rules);
      this.rules = data.rules || [];
      this.render();
    }

    render() {
      this.list.replaceChildren();
      const query = this.search.value.trim().toLowerCase();
      const matches = this.rules
        .map((rule, index) => ({ rule, index }))
        .filter(({ rule }) => !query || `${rule.pattern} ${rule.replacement}`.toLowerCase().includes(query));
      if (!matches.length) {
        this.list.textContent = this.rules.length ? "No matching rules." : "No glossary rules yet.";
        return;
      }
      matches.forEach(({ rule, index }) => {
        const row = this.document.createElement("div");
        row.className = "glossary-row";
        const words = this.document.createElement("span");
        words.textContent = `${rule.pattern} → ${rule.replacement}`;
        const button = this.document.createElement("button");
        button.type = "button";
        button.className = "text-button";
        button.textContent = "Remove";
        button.addEventListener("click", () => this.remove(index));
        row.append(words, button);
        this.list.appendChild(row);
      });
    }
  }

  const api = { GlossaryController };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
