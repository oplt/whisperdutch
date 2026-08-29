class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.sourceSampleRate = sampleRate;
    this.sourceBufferSize = 4096;
    this.sourceBuffer = new Float32Array(this.sourceBufferSize);
    this.sourceOffset = 0;
    this.squareSum = 0;
    this.port.onmessage = event => {
      if (event.data?.type === "config") {
        this.targetSampleRate = Number(event.data.targetSampleRate) || 16000;
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const channel = input[0];
    for (let i = 0; i < channel.length; i += 1) {
      this.sourceBuffer[this.sourceOffset] = channel[i];
      this.squareSum += channel[i] * channel[i];
      this.sourceOffset += 1;
      if (this.sourceOffset >= this.sourceBufferSize) {
        this.flush();
      }
    }
    return true;
  }

  flush() {
    if (this.sourceOffset === 0) return;
    const source = this.sourceBuffer.subarray(0, this.sourceOffset);
    const pcm = resampleToPCM16LE(source, this.sourceSampleRate, this.targetSampleRate);
    const level = Math.min(1, Math.sqrt(this.squareSum / this.sourceOffset) * 4);
    this.port.postMessage({ pcm, level }, [pcm]);
    this.sourceOffset = 0;
    this.squareSum = 0;
  }
}

function resampleToPCM16LE(input, sourceRate, targetRate) {
  const ratio = sourceRate / targetRate;
  const outputLength = Math.floor(input.length / ratio);
  const output = new ArrayBuffer(outputLength * 2);
  const view = new DataView(output);

  for (let i = 0; i < outputLength; i += 1) {
    const srcIndex = i * ratio;
    const index0 = Math.floor(srcIndex);
    const index1 = Math.min(index0 + 1, input.length - 1);
    const frac = srcIndex - index0;
    const sample = Math.max(-1, Math.min(1, input[index0] * (1 - frac) + input[index1] * frac));
    const int16 = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    view.setInt16(i * 2, int16, true);
  }

  return output;
}

registerProcessor("pcm-worklet", PCMWorkletProcessor);
