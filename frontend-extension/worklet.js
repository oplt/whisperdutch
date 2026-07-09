class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.sourceSampleRate = sampleRate;
    this.sourceBufferSize = 4096;
    this.sourceBuffer = new Float32Array(this.sourceBufferSize);
    this.sourceOffset = 0;
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
    const pcm16 = float32ToPCM16LE(resampleLinear(source, this.sourceSampleRate, this.targetSampleRate));
    this.port.postMessage(pcm16, [pcm16]);
    this.sourceBuffer = new Float32Array(this.sourceBufferSize);
    this.sourceOffset = 0;
  }
}

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

registerProcessor("pcm-worklet", PCMWorkletProcessor);
