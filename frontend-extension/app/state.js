(function (root) {
  const TRANSITIONS = Object.freeze({
    idle: ["starting-backend"],
    "starting-backend": ["connecting", "stopping", "error"],
    connecting: ["capturing", "reconnecting", "stopping", "error"],
    capturing: ["paused", "reconnecting", "stopping", "error"],
    paused: ["capturing", "reconnecting", "stopping", "error"],
    reconnecting: ["capturing", "stopping", "error"],
    stopping: ["idle", "error"],
    error: ["starting-backend", "idle"]
  });

  class AppState {
    constructor(initial = "idle") {
      if (!TRANSITIONS[initial]) throw new Error(`Unknown application state: ${initial}`);
      this.value = initial;
      this.detail = "";
      this.error = "";
      this.generation = 0;
      this.listeners = new Set();
    }

    canTransition(next) {
      return TRANSITIONS[this.value].includes(next);
    }

    transition(next, detail = "") {
      if (!this.canTransition(next)) {
        throw new Error(`Invalid application transition: ${this.value} -> ${next}`);
      }
      this.value = next;
      this.detail = detail;
      this.error = next === "error" ? detail : "";
      this.emit();
      return this.value;
    }

    begin(next, detail = "") {
      const generation = ++this.generation;
      this.transition(next, detail);
      return generation;
    }

    invalidate() {
      this.generation += 1;
      return this.generation;
    }

    owns(generation) {
      return generation === this.generation;
    }

    retry(detail = "Starting local service") {
      if (this.value !== "error") throw new Error("Retry is only valid from error state");
      return this.begin("starting-backend", detail);
    }

    reset(detail = "Ready") {
      if (this.value === "idle") {
        this.detail = detail;
        this.emit();
        return this.value;
      }
      this.transition("idle", detail);
      return this.value;
    }

    subscribe(listener) {
      this.listeners.add(listener);
      listener(this.snapshot());
      return () => this.listeners.delete(listener);
    }

    snapshot() {
      return {
        value: this.value,
        detail: this.detail,
        error: this.error,
        generation: this.generation
      };
    }

    emit() {
      const snapshot = this.snapshot();
      this.listeners.forEach(listener => listener(snapshot));
    }
  }

  const api = { AppState, TRANSITIONS };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
