(function (root) {
  function normalizeText(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  function mergeByWordOverlap(left, right) {
    const leftWords = left.split(/\s+/).filter(Boolean);
    const rightWords = right.split(/\s+/).filter(Boolean);
    const maxOverlap = Math.min(10, leftWords.length, rightWords.length);
    for (let n = maxOverlap; n > 0; n -= 1) {
      const a = leftWords.slice(-n).join(" ").toLowerCase();
      const b = rightWords.slice(0, n).join(" ").toLowerCase();
      if (a === b) return [...leftWords, ...rightWords.slice(n)].join(" ");
    }
    return right.length > left.length ? right : left;
  }

  function stabilizePartial(previous, next) {
    previous = normalizeText(previous);
    next = normalizeText(next);
    if (!next) return previous;
    if (!previous) return next;

    const prevLower = previous.toLowerCase();
    const nextLower = next.toLowerCase();

    if (nextLower === prevLower) return previous;
    if (nextLower.startsWith(prevLower)) return next;
    if (prevLower.includes(nextLower) && next.length + 10 < previous.length) return previous;
    if (next.length < previous.length * 0.72 && !prevLower.endsWith(".") && !prevLower.endsWith("?") && !prevLower.endsWith("!")) {
      return previous;
    }

    const merged = mergeByWordOverlap(previous, next);
    if (merged.length >= Math.max(previous.length, next.length) - 4) return merged;
    return next;
  }

  function formatTime(ms, includeHours) {
    const totalMs = Math.max(0, Math.round(ms));
    const hours = Math.floor(totalMs / 3600000);
    const minutes = Math.floor((totalMs % 3600000) / 60000);
    const seconds = Math.floor((totalMs % 60000) / 1000);
    const millis = totalMs % 1000;
    const hh = String(hours).padStart(2, "0");
    const mm = String(minutes).padStart(2, "0");
    const ss = String(seconds).padStart(2, "0");
    const mmm = String(millis).padStart(3, "0");
    return includeHours ? `${hh}:${mm}:${ss}.${mmm}` : `${mm}:${ss}.${mmm}`;
  }

  function toTxt(rows) {
    return rows.map(item => [
      `[${formatTime(item.startMs, false)}]`,
      item.dutch,
      item.translation
    ].filter(Boolean).join("\n")).join("\n\n");
  }

  function toVtt(rows) {
    return `WEBVTT\n\n${rows.map(item => `${formatTime(item.startMs, true)} --> ${formatTime(item.endMs, true)}\n${item.dutch}\n${item.translation}`.trim()).join("\n\n")}\n`;
  }

  function toSrt(rows) {
    return `${rows.map((item, index) => `${index + 1}\n${formatTime(item.startMs, true).replace(".", ",")} --> ${formatTime(item.endMs, true).replace(".", ",")}\n${item.dutch}\n${item.translation}`.trim()).join("\n\n")}\n`;
  }

  const api = { normalizeText, mergeByWordOverlap, stabilizePartial, formatTime, toTxt, toVtt, toSrt };
  root.SubtitleRenderer = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
