const DEFAULT_WORKLET_BATCH_DURATION_MS = 20;
const MIN_WORKLET_BATCH_DURATION_MS = 10;
const MAX_WORKLET_BATCH_DURATION_MS = 80;
const PCM16_MONO_16K_BYTES_PER_SECOND = 16000 * 2;

function clampBatchDurationMs(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return DEFAULT_WORKLET_BATCH_DURATION_MS;
  return Math.max(MIN_WORKLET_BATCH_DURATION_MS, Math.min(MAX_WORKLET_BATCH_DURATION_MS, numeric));
}

function sourceBufferSizeForRate(sourceSampleRate, batchDurationMs = DEFAULT_WORKLET_BATCH_DURATION_MS) {
  return Math.max(128, Math.round(sourceSampleRate * batchDurationMs / 1000));
}

function maxPcmOutputSamples(sourceSamples, sourceRate, targetRate = 16000) {
  if (sourceSamples <= 0) return 0;
  const ratio = sourceRate / targetRate;
  return Math.max(0, Math.ceil((sourceSamples - 1) / ratio));
}

function messagesPerSecond(batchDurationMs = DEFAULT_WORKLET_BATCH_DURATION_MS) {
  return 1000 / batchDurationMs;
}

function theoreticalCaptureLatencyMs(batchDurationMs = DEFAULT_WORKLET_BATCH_DURATION_MS) {
  return batchDurationMs / 2;
}

function pcmBytesPerMessage(sourceSampleRate, batchDurationMs = DEFAULT_WORKLET_BATCH_DURATION_MS, targetRate = 16000) {
  const sourceSamples = sourceBufferSizeForRate(sourceSampleRate, batchDurationMs);
  return maxPcmOutputSamples(sourceSamples, sourceSampleRate, targetRate) * 2;
}

class StreamingPCM16Resampler {
  constructor(sourceRate, targetRate) {
    if (!(sourceRate > 0) || !(targetRate > 0)) throw new RangeError("Sample rates must be positive.");
    this.sourceRate = sourceRate;
    this.targetRate = targetRate;
    this.ratio = sourceRate / targetRate;
    this.sourcePosition = 0;
    this.previousSample = 0;
    this._outputScratch = null;
  }

  process(input, into = null) {
    if (!input?.length) return new ArrayBuffer(0);

    const endPosition = input.length - 1;
    const outputLength = this.sourcePosition < endPosition
      ? Math.ceil((endPosition - this.sourcePosition) / this.ratio)
      : 0;
    if (outputLength === 0) return new ArrayBuffer(0);

    let output;
    let allocated = false;
    if (into instanceof Int16Array && into.length >= outputLength) {
      output = into.subarray(0, outputLength);
    } else {
      output = new Int16Array(outputLength);
      allocated = true;
    }

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

    if (allocated) {
      return output.buffer;
    }

    if (!this._outputScratch || this._outputScratch.length !== outputLength) {
      this._outputScratch = new Int16Array(outputLength);
    }
    this._outputScratch.set(output);
    return this._outputScratch.buffer;
  }
}

const WorkletProcessorBase = typeof AudioWorkletProcessor === "undefined" ? class {} : AudioWorkletProcessor;

class PCMWorkletProcessor extends WorkletProcessorBase {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.sourceSampleRate = typeof sampleRate === "undefined" ? 48000 : sampleRate;
    this.batchDurationMs = DEFAULT_WORKLET_BATCH_DURATION_MS;
    this.sourceBuffer = new Float32Array(sourceBufferSizeForRate(this.sourceSampleRate, this.batchDurationMs));
    this.sourceOffset = 0;
    this.squareSum = 0;
    this._pcmOutputCapacity = 0;
    this._pcmOutputBuffer = null;
    this.resampler = new StreamingPCM16Resampler(this.sourceSampleRate, this.targetSampleRate);
    this.port.onmessage = event => {
      if (event.data?.type !== "config") return;
      const targetSampleRate = Number(event.data.targetSampleRate) || 16000;
      if (targetSampleRate !== this.targetSampleRate) {
        this.targetSampleRate = targetSampleRate;
        this.resampler = new StreamingPCM16Resampler(this.sourceSampleRate, this.targetSampleRate);
        this._pcmOutputCapacity = 0;
        this._pcmOutputBuffer = null;
      }
      const batchDurationMs = clampBatchDurationMs(event.data.batchDurationMs);
      if (batchDurationMs !== this.batchDurationMs) {
        this.batchDurationMs = batchDurationMs;
        this.sourceBuffer = new Float32Array(sourceBufferSizeForRate(this.sourceSampleRate, this.batchDurationMs));
        this.sourceOffset = 0;
        this.squareSum = 0;
        this._pcmOutputCapacity = 0;
        this._pcmOutputBuffer = null;
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
      if (this.sourceOffset >= this.sourceBuffer.length) {
        this.flush();
      }
    }
    return true;
  }

  flush() {
    if (this.sourceOffset === 0) return;

    const sourceLength = this.sourceOffset;
    const requiredCapacity = maxPcmOutputSamples(sourceLength, this.sourceSampleRate, this.targetSampleRate);
    if (!this._pcmOutputBuffer || this._pcmOutputBuffer.length < requiredCapacity) {
      this._pcmOutputCapacity = requiredCapacity;
      this._pcmOutputBuffer = new Int16Array(requiredCapacity);
    }

    const pcm = this.resampler.process(this.sourceBuffer.subarray(0, sourceLength), this._pcmOutputBuffer);
    const level = Math.min(1, Math.sqrt(this.squareSum / sourceLength) * 4);
    this.port.postMessage({ pcm, level }, [pcm]);
    this.sourceOffset = 0;
    this.squareSum = 0;
  }
}

const api = {
  PCMWorkletProcessor,
  StreamingPCM16Resampler,
  DEFAULT_WORKLET_BATCH_DURATION_MS,
  MIN_WORKLET_BATCH_DURATION_MS,
  MAX_WORKLET_BATCH_DURATION_MS,
  PCM16_MONO_16K_BYTES_PER_SECOND,
  clampBatchDurationMs,
  sourceBufferSizeForRate,
  maxPcmOutputSamples,
  messagesPerSecond,
  theoreticalCaptureLatencyMs,
  pcmBytesPerMessage
};

if (typeof registerProcessor === "function") registerProcessor("pcm-worklet", PCMWorkletProcessor);
if (typeof module !== "undefined") {
  module.exports = api;
}
