const WORKLET_BATCH_DURATION_MS = 20;

class StreamingPCM16Resampler {
  constructor(sourceRate, targetRate) {
    if (!(sourceRate > 0) || !(targetRate > 0)) throw new RangeError("Sample rates must be positive.");
    this.sourceRate = sourceRate;
    this.targetRate = targetRate;
    this.ratio = sourceRate / targetRate;
    this.sourcePosition = 0;
    this.previousSample = 0;
  }

  process(input) {
    if (!input?.length) return new ArrayBuffer(0);

    const endPosition = input.length - 1;
    const outputLength = this.sourcePosition < endPosition
      ? Math.ceil((endPosition - this.sourcePosition) / this.ratio)
      : 0;
    const output = new Int16Array(outputLength);
    let position = this.sourcePosition;

    for (let index = 0; index < outputLength; index += 1) {
      const sourceIndex = Math.floor(position);
      const fraction = position - sourceIndex;
      const first = sourceIndex < 0 ? this.previousSample : input[sourceIndex];
      const second = input[sourceIndex + 1];
      const sample = Math.max(-1, Math.min(1, first + (second - first) * fraction));
      output[index] = sample < 0 ? Math.round(sample * 0x8000) : Math.round(sample * 0x7fff);
      position += this.ratio;
    }

    this.sourcePosition = position - input.length;
    this.previousSample = input[input.length - 1];
    return output.buffer;
  }
}

const WorkletProcessorBase = typeof AudioWorkletProcessor === "undefined" ? class {} : AudioWorkletProcessor;

class PCMWorkletProcessor extends WorkletProcessorBase {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.sourceSampleRate = sampleRate;
    this.sourceBufferSize = Math.max(128, Math.round(this.sourceSampleRate * WORKLET_BATCH_DURATION_MS / 1000));
    this.sourceBuffer = new Float32Array(this.sourceBufferSize);
    this.sourceOffset = 0;
    this.squareSum = 0;
    this.resampler = new StreamingPCM16Resampler(this.sourceSampleRate, this.targetSampleRate);
    this.port.onmessage = event => {
      if (event.data?.type === "config") {
        const targetSampleRate = Number(event.data.targetSampleRate) || 16000;
        if (targetSampleRate !== this.targetSampleRate) {
          this.targetSampleRate = targetSampleRate;
          this.resampler = new StreamingPCM16Resampler(this.sourceSampleRate, this.targetSampleRate);
        }
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
    const pcm = this.resampler.process(source);
    const level = Math.min(1, Math.sqrt(this.squareSum / this.sourceOffset) * 4);
    this.port.postMessage({ pcm, level }, [pcm]);
    this.sourceOffset = 0;
    this.squareSum = 0;
  }
}

if (typeof registerProcessor === "function") registerProcessor("pcm-worklet", PCMWorkletProcessor);
if (typeof module !== "undefined") {
  module.exports = { PCMWorkletProcessor, StreamingPCM16Resampler, WORKLET_BATCH_DURATION_MS };
}
