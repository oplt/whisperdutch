(function (root) {
  function resampleLinear(input, sourceRate, targetRate) {
    if (!input || input.length === 0) return new Float32Array(0);
    if (sourceRate === targetRate) return input;

    const ratio = sourceRate / targetRate;
    const outputLength = Math.floor(input.length / ratio);
    const output = new Float32Array(outputLength);

    for (let i = 0; i < outputLength; i += 1) {
      const srcIndex = i * ratio;
      const index0 = Math.floor(srcIndex);
      const index1 = Math.min(index0 + 1, input.length - 1);
      const frac = srcIndex - index0;
      output[i] = input[index0] * (1 - frac) + input[index1] * frac;
    }

    return output;
  }

  function float32ToPCM16LE(samples) {
    const buffer = new ArrayBuffer(samples.length * 2);
    const view = new DataView(buffer);
    for (let i = 0; i < samples.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, samples[i]));
      const int16 = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      view.setInt16(i * 2, int16, true);
    }
    return buffer;
  }

  const api = { resampleLinear, float32ToPCM16LE };
  root.AudioClient = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
