/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * useSpeechEngine.js
 * The frontend half of the local speech pipeline: capture audio, post
 * it, poll for events.
 *
 * The polling pattern is carried over deliberately, as the architecture
 * notes asked. The backend exposes three monotonically-increasing
 * counters - wake_seq, stop_seq, transcript_seq - and this composable
 * keeps its own last-seen value for each. An event is "a counter moved",
 * not "a flag is true", which means a slow poll or two events landing
 * between polls can't lose anything. A boolean would.
 *
 * What changed from the reference: this is a composable with reactive
 * refs rather than setInterval plus module-level variables, so a
 * component can render `mode` or `partialText` directly and teardown is
 * tied to the component's lifetime instead of remembering to clear a
 * timer.
 */

import { onScopeDispose, readonly, ref } from 'vue'
import { api } from '@/api/client'

const POLL_INTERVAL_MS = 200
const TARGET_RATE = 16000

export function useSpeechEngine() {
  const mode = ref('asleep')          // 'asleep' | 'awake' | 'suppressed'
  const partialText = ref('')         // live preview of what Vosk hears
  const ready = ref(false)
  const error = ref(null)
  const capturing = ref(false)
  const micError = ref(null)

  // Last-seen counters. Initialised from the first poll rather than
  // zero, so events that happened before this component existed don't
  // all fire at once on mount.
  let seen = null
  let pollTimer = null
  let pollInFlight = null

  const handlers = { wake: null, stop: null, transcript: null }

  let audioContext = null
  let workletNode = null
  let micStream = null
  let micSource = null

  /** onWake / onStop / onTranscript(text) */
  function on(event, fn) {
    if (event in handlers) handlers[event] = fn
  }

  async function poll() {
    let data
    try {
      data = await api.speechStatus()
    } catch {
      // A failed poll is almost always a restart or a momentary blip.
      // Keep polling rather than tearing down a working voice session.
      return
    }

    ready.value = data.ready
    error.value = data.error
    mode.value = data.mode
    partialText.value = data.partial_text || ''

    if (seen === null) {
      seen = {
        wake: data.wake_seq,
        stop: data.stop_seq,
        transcript: data.transcript_seq,
      }
      return
    }

    // Compare, then record, then dispatch - so a handler that throws
    // can't cause the same event to fire again on the next poll.
    const firedWake = data.wake_seq > seen.wake
    const firedStop = data.stop_seq > seen.stop
    const firedTranscript = data.transcript_seq > seen.transcript
    const transcript = data.transcript_text

    seen.wake = data.wake_seq
    seen.stop = data.stop_seq
    seen.transcript = data.transcript_seq

    if (firedWake) handlers.wake?.()
    if (firedStop) handlers.stop?.()
    if (firedTranscript && transcript) handlers.transcript?.(transcript)
  }

  function startPolling() {
    if (pollTimer) return
    // A chained timeout rather than setInterval: if a poll is ever
    // slower than the interval, setInterval would stack requests and
    // they'd race to report the same counter move. Only one poll is ever
    // in flight here - the same reasoning the reference applied to its
    // camera capture loops.
    const tick = async () => {
      pollInFlight = poll()
      await pollInFlight
      pollInFlight = null
      if (pollTimer !== null) pollTimer = setTimeout(tick, POLL_INTERVAL_MS)
    }
    pollTimer = setTimeout(tick, 0)
  }

  function stopPolling() {
    if (pollTimer) clearTimeout(pollTimer)
    pollTimer = null
    seen = null
  }

  /** The counter the backend should compare a sleep request against. */
  const lastWakeSeq = () => seen?.wake ?? null

  async function startCapture() {
    if (capturing.value) return true
    micError.value = null

    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          // Ask the browser for the cleanup it's good at. The models
          // benefit and it costs nothing; a browser that ignores these
          // just gives us raw audio, which still works.
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      })
    } catch (err) {
      micError.value =
        err?.name === 'NotAllowedError'
          ? 'I need microphone permission to use voice chat.'
          : "I can't access a microphone - check that one is connected."
      return false
    }

    try {
      // Ask for 16kHz directly. Browsers often ignore this and give
      // 48kHz anyway, which is exactly why the worklet resamples from
      // whatever `sampleRate` actually turns out to be rather than
      // assuming it got what it asked for.
      audioContext = new AudioContext({ sampleRate: TARGET_RATE })
      const workletUrl = new URL('@/worklets/pcm-processor.js', import.meta.url)
      await audioContext.audioWorklet.addModule(workletUrl)

      micSource = audioContext.createMediaStreamSource(micStream)
      workletNode = new AudioWorkletNode(audioContext, 'pcm-processor')

      workletNode.port.onmessage = (event) => {
        // Fire and forget. A dropped chunk is better than a backlog:
        // awaiting here would let slow requests queue up behind each
        // other and push recognition further behind real time.
        api.speechAudio(event.data).catch(() => {})
      }

      micSource.connect(workletNode)
      // The worklet produces no output, but some browsers won't pull on
      // a node that isn't connected to anything. A zero-gain node to the
      // destination keeps it running without making a sound.
      const silence = audioContext.createGain()
      silence.gain.value = 0
      workletNode.connect(silence).connect(audioContext.destination)

      if (audioContext.state === 'suspended') await audioContext.resume()
      capturing.value = true
      return true
    } catch (err) {
      micError.value = `Couldn't start audio capture: ${err?.message || err}`
      await stopCapture()
      return false
    }
  }

  async function stopCapture() {
    capturing.value = false
    try { workletNode?.port.postMessage('stop') } catch { /* already gone */ }
    try { workletNode?.disconnect() } catch { /* already gone */ }
    try { micSource?.disconnect() } catch { /* already gone */ }
    // Releasing every track is what actually turns the mic indicator
    // off. Closing the context alone doesn't.
    micStream?.getTracks().forEach((t) => t.stop())
    try { await audioContext?.close() } catch { /* already closed */ }
    workletNode = micSource = micStream = audioContext = null
  }

  const suppress = () => api.speechControl('suppress').catch(() => {})
  const unsuppress = () => api.speechControl('unsuppress').catch(() => {})
  const sleep = () => api.speechControl('sleep', lastWakeSeq()).catch(() => {})

  async function stop() {
    stopPolling()
    await stopCapture()
  }

  onScopeDispose(() => { stop() })

  return {
    mode: readonly(mode),
    partialText: readonly(partialText),
    ready: readonly(ready),
    error: readonly(error),
    capturing: readonly(capturing),
    micError: readonly(micError),
    on, startPolling, stopPolling, startCapture, stopCapture,
    suppress, unsuppress, sleep, stop, lastWakeSeq,
  }
}
