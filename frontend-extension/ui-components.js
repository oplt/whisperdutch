(function (root) {
  function setBadgeState(element, state) {
    if (element) element.dataset.state = state;
  }

  function setBadgeValue(valueElement, text, state) {
    if (!valueElement) return;
    valueElement.textContent = text;
    setBadgeState(valueElement.closest(".status-badge"), state);
  }

  function createSubtitleCard(item) {
    const fragment = document.createDocumentFragment();
    const dutchCell = document.createElement("div");
    dutchCell.className = "subtitle-cell dutch";
    dutchCell.textContent = item.dutch;

    const translationCell = document.createElement("div");
    translationCell.className = item.pending ? "subtitle-cell translation pending" : "subtitle-cell translation";
    translationCell.textContent = item.translation;

    fragment.appendChild(dutchCell);
    fragment.appendChild(translationCell);
    return fragment;
  }

  const api = { createSubtitleCard, setBadgeState, setBadgeValue };
  root.SubtitleUI = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
