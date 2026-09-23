const DEFAULT_WORKLET_BATCH_DURATION_MS = 20;
const MIN_WORKLET_BATCH_DURATION_MS = 10;
const MAX_WORKLET_BATCH_DURATION_MS = 80;
const PCM16_MONO_16K_BYTES_PER_SECOND = 16000 * 2;
const DEFAULT_ANTIALIAS_TAPS = 15;

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
  return Math.max(0, Math.ceil((sourceSamples - 1) / ratio) + 2);
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

/**
 * Hamming-windowed sinc low-pass. cutoffNormalized is f_c / sourceRate (0 < c < 0.5).
 * Designed so stopband starts near the post-downsample Nyquist.
 */
function designLowpassTaps(numTaps, cutoffNormalized) {
  const taps = numTaps | 0;
  if (taps < 3 || taps % 2 === 0) {
    throw new RangeError("numTaps must be an odd integer >= 3");
  }
  const cutoff = Math.max(0.01, Math.min(0.49, cutoffNormalized));
  const mid = (taps - 1) / 2;
  const coefficients = new Float32Array(taps);
  let sum = 0;
  for (let n = 0; n < taps; n += 1) {
    const x = n - mid;
    const sinc = x === 0 ? 2 * cutoff : Math.sin(2 * Math.PI * cutoff * x) / (Math.PI * x);
    const window = 0.54 - 0.46 * Math.cos((2 * Math.PI * n) / (taps - 1));
    coefficients[n] = sinc * window;
    sum += coefficients[n];
  }
  for (let n = 0; n < taps; n += 1) coefficients[n] /= sum;
  return coefficients;
}

function mixInputFrame(channels, frameIndex) {
  // Prefer the loudest channel this frame without averaging (averaging diluted
  // tabCapture speech when one channel was silent/noise and hurt ASR).
  if (!channels?.length) return 0;
  if (channels.length === 1) {
    const only = channels[0];
    return only && frameIndex < only.length ? only[frameIndex] : 0;
  }

  let best = 0;
  let bestAbs = -1;
  let sawSample = false;
  for (let channelIndex = 0; channelIndex < channels.length; channelIndex += 1) {
    const channel = channels[channelIndex];
    if (!channel || frameIndex >= channel.length) continue;
    const sample = channel[frameIndex];
    const abs = sample < 0 ? -sample : sample;
    // Prefer channel 0 on ties so Chromium tabCapture stays stable.
    if (!sawSample || abs > bestAbs || (abs === bestAbs && channelIndex === 0)) {
      best = sample;
      bestAbs = abs;
      sawSample = true;
    }
  }
  return sawSample ? best : 0;
}

function pickCaptureChannel(channels, frameCount) {
  if (!channels?.length) return 0;
  if (channels.length === 1) return 0;
  let bestIndex = 0;
  let bestEnergy = -1;
  const frames = Math.max(0, frameCount | 0);
  for (let channelIndex = 0; channelIndex < channels.length; channelIndex += 1) {
    const channel = channels[channelIndex];
    if (!channel?.length) continue;
    let energy = 0;
    const limit = Math.min(frames || channel.length, channel.length);
    for (let i = 0; i < limit; i += 8) {
      const sample = channel[i];
      energy += sample * sample;
    }
    if (energy > bestEnergy) {
      bestEnergy = energy;
      bestIndex = channelIndex;
    }
  }
  return bestIndex;
}

class StreamingPCM16Resampler {
  constructor(sourceRate, targetRate, options = {}) {
    if (!(sourceRate > 0) || !(targetRate > 0)) throw new RangeError("Sample rates must be positive.");
    this.sourceRate = sourceRate;
    this.targetRate = targetRate;
    this.ratio = sourceRate / targetRate;
    this.sourcePosition = 0;
    this.previousSample = 0;
    this._outputScratch = null;
    this._filterScratch = null;

    const downsample = targetRate < sourceRate;
    const tapCount = Number(options.antialiasTaps);
    // Keep the low-pass enabled for live downsampling. Its 15-tap default adds
    // sub-millisecond group delay while preventing >8 kHz content from folding
    // into Whisper's speech band. Callers can still opt out explicitly.
    this.antialiasEnabled = options.antialias !== false && downsample;
    if (this.antialiasEnabled) {
      // Cut slightly below target Nyquist expressed at the source sample rate.
      const cutoff = Math.min(0.45, (0.90 * (targetRate / 2)) / sourceRate);
      const taps = Number.isFinite(tapCount) && tapCount >= 3 ? (tapCount | 0) | 1 : DEFAULT_ANTIALIAS_TAPS;
      this._taps = designLowpassTaps(taps, cutoff);
      this._delay = new Float32Array(this._taps.length - 1);
      this._delayFilled = 0;
    } else {
      this._taps = null;
      this._delay = null;
      this._delayFilled = 0;
    }
  }

  _ensureFilterScratch(length) {
    if (!this._filterScratch || this._filterScratch.length < length) {
      this._filterScratch = new Float32Array(length);
    }
    return this._filterScratch;
  }

  _filterChunk(input) {
    if (!this.antialiasEnabled) return input;
    const taps = this._taps;
    const delay = this._delay;
    const tapCount = taps.length;
    const history = tapCount - 1;
    const out = this._ensureFilterScratch(input.length);
    for (let i = 0; i < input.length; i += 1) {
      let acc = 0;
      for (let t = 0; t < tapCount; t += 1) {
        const sourceIndex = i - t;
        const sample = sourceIndex >= 0
          ? input[sourceIndex]
          : (this._delayFilled > 0 ? delay[history + sourceIndex] : 0);
        acc += taps[t] * sample;
      }
      out[i] = acc;
    }
    // Update delay with the trailing history samples from this chunk.
    if (input.length >= history) {
      delay.set(input.subarray(input.length - history));
      this._delayFilled = history;
    } else if (input.length > 0) {
      const keep = history - input.length;
      if (keep > 0) delay.copyWithin(0, input.length);
      delay.set(input, keep);
      this._delayFilled = Math.min(history, this._delayFilled + input.length);
    }
    return out.subarray(0, input.length);
  }

  process(input, into = null) {
    if (!input?.length) return new ArrayBuffer(0);

    const filtered = this._filterChunk(input);
    const endPosition = filtered.length - 1;
    const outputLength = this.sourcePosition < endPosition
      ? Math.ceil((endPosition - this.sourcePosition) / this.ratio)
      : 0;
    if (outputLength === 0) {
      if (filtered.length) this.previousSample = filtered[filtered.length - 1];
      this.sourcePosition = Math.max(-1, this.sourcePosition - filtered.length);
      return new ArrayBuffer(0);
    }

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
      const first = sourceIndex < 0 ? this.previousSample : filtered[sourceIndex];
      const second = filtered[Math.min(sourceIndex + 1, filtered.length - 1)];
      const sample = Math.max(-1, Math.min(1, first + (second - first) * fraction));
      output[index] = sample < 0 ? Math.round(sample * 0x8000) : Math.round(sample * 0x7fff);
      position += this.ratio;
    }

    this.sourcePosition = position - filtered.length;
    this.previousSample = filtered[filtered.length - 1];

    if (allocated) {
      return output.buffer.slice(output.byteOffset, output.byteOffset + output.byteLength);
    }

    // Copy into a dedicated transferable buffer so `into` stays usable after postMessage transfer.
    const transferable = new ArrayBuffer(outputLength * 2);
    new Int16Array(transferable).set(output);
    return transferable;
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
    this.port.onmessage = event => this._onPortMessage(event);
  }

  _onPortMessage(event) {
    const data = event.data || {};
    if (data.type === "flush") {
      this.flush();
      return;
    }
    if (data.type !== "config") return;
    const targetSampleRate = Number(data.targetSampleRate) || 16000;
    if (targetSampleRate !== this.targetSampleRate) {
      this.targetSampleRate = targetSampleRate;
      this.resampler = new StreamingPCM16Resampler(this.sourceSampleRate, this.targetSampleRate);
      this._pcmOutputCapacity = 0;
      this._pcmOutputBuffer = null;
    }
    const batchDurationMs = clampBatchDurationMs(data.batchDurationMs);
    if (batchDurationMs !== this.batchDurationMs) {
      this.batchDurationMs = batchDurationMs;
      this.sourceBuffer = new Float32Array(sourceBufferSizeForRate(this.sourceSampleRate, this.batchDurationMs));
      this.sourceOffset = 0;
      this.squareSum = 0;
      this._pcmOutputCapacity = 0;
      this._pcmOutputBuffer = null;
    }
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const frameCount = input[0]?.length || input.find(channel => channel && channel.length)?.length || 0;
    if (!frameCount) return true;

    const channelIndex = pickCaptureChannel(input, frameCount);
    const channel = input[channelIndex];
    for (let i = 0; i < frameCount; i += 1) {
      const sample = channel && i < channel.length ? channel[i] : mixInputFrame(input, i);
      this.sourceBuffer[this.sourceOffset] = sample;
      this.squareSum += sample * sample;
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
    // Transfer a standalone buffer; keep _pcmOutputBuffer attached for reuse.
    this.port.postMessage({ pcm, level }, [pcm]);
    this.sourceOffset = 0;
    this.squareSum = 0;
  }
}

const api = {
  PCMWorkletProcessor,
  StreamingPCM16Resampler,
  designLowpassTaps,
  mixInputFrame,
  pickCaptureChannel,
  DEFAULT_WORKLET_BATCH_DURATION_MS,
  MIN_WORKLET_BATCH_DURATION_MS,
  MAX_WORKLET_BATCH_DURATION_MS,
  DEFAULT_ANTIALIAS_TAPS,
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
