/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * pcm-processor.js
 * AudioWorkletProcessor: microphone input -> 16kHz mono 16-bit PCM.
 *
 * Both speech models want 16kHz mono int16. Browser capture is wider -
 * typically 44.1 or 48kHz float32 - so something has to resample. Doing
 * it here keeps the backend free of any audio-format negotiation: it
 * receives exactly the bytes vosk and whisper want and nothing else.
 *
 * An AudioWorklet rather than the deprecated ScriptProcessorNode: this
 * runs on the audio rendering thread, so resampling never competes with
 * Vue's rendering or a pending fetch, and a busy main thread can't cause
 * dropouts in what Quietude hears.
 *
 * Chunks are posted as transferable ArrayBuffers, so the bytes are moved
 * to the main thread rather than copied.
 */

const TARGET_RATE = 16000
const CHUNK_MS = 250

class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    // sampleRate is a global in the AudioWorkletGlobalScope - the real
    // hardware rate, which is not necessarily what was requested.
    this.ratio = sampleRate / TARGET_RATE
    this.chunkSamples = Math.round((TARGET_RATE * CHUNK_MS) / 1000)
    this.out = new Int16Array(this.chunkSamples)
    this.outIndex = 0
    // Fractional read position into the input stream, carried across
    // process() calls so the resampler doesn't restart every 128 frames
    // and introduce a periodic click.
    this.readPos = 0
    this.tail = new Float32Array(0)
    this.running = true

    this.port.onmessage = (event) => {
      if (event.data === 'stop') this.running = false
    }
  }

  process(inputs) {
    if (!this.running) return false // returning false lets the node be collected

    const channel = inputs[0]?.[0]
    if (!channel || channel.length === 0) return true

    // Prepend whatever was left over from last time, so interpolation
    // can span the boundary between two process() calls.
    let buf
    if (this.tail.length) {
      buf = new Float32Array(this.tail.length + channel.length)
      buf.set(this.tail, 0)
      buf.set(channel, this.tail.length)
    } else {
      buf = channel
    }

    // Linear interpolation down to the target rate. Not a polyphase
    // filter - for speech models fed 16kHz this is inaudibly adequate,
    // and it costs a multiply per output sample.
    let pos = this.readPos
    while (pos < buf.length - 1) {
      const i = Math.floor(pos)
      const frac = pos - i
      const sample = buf[i] * (1 - frac) + buf[i + 1] * frac

      // Clamp before scaling: a float sample can exceed +/-1 and would
      // otherwise wrap around into loud noise once cast to int16.
      const clamped = Math.max(-1, Math.min(1, sample))
      this.out[this.outIndex++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff

      if (this.outIndex >= this.chunkSamples) {
        const copy = this.out.slice(0)
        this.port.postMessage(copy.buffer, [copy.buffer])
        this.outIndex = 0
      }
      pos += this.ratio
    }

    // Keep the unconsumed remainder, and the fractional offset into it.
    const consumed = Math.floor(pos)
    this.tail = buf.slice(consumed)
    this.readPos = pos - consumed

    return true
  }
}

registerProcessor('pcm-processor', PCMProcessor)
