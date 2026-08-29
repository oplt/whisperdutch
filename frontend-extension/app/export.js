(function (root) {
  function exportTranscript(items, format, renderer = root.SubtitleRenderer, documentRef = root.document) {
    const rows = items.filter(item => item.dutch || item.translation);
    if (!rows.length) return false;
    const content = format === "srt"
      ? renderer.toSrt(rows)
      : format === "vtt"
        ? renderer.toVtt(rows)
        : renderer.toTxt(rows);
    const mime = format === "txt" ? "text/plain" : "text/vtt";
    const blob = new Blob([content], { type: `${mime};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const link = documentRef.createElement("a");
    link.href = url;
    link.download = `dutch-subtitles-${new Date().toISOString().replace(/[:.]/g, "-")}.${format}`;
    documentRef.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    return true;
  }

  const api = { exportTranscript };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
