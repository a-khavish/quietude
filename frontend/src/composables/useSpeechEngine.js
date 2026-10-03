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

// Which input, and whether to let the browser clean it up. Kept in the
// browser rather than in the profile on purpose: both are facts about
// this machine's hardware, not about the person, and carrying them to
// another machine would be carrying the wrong answer.
const DEVICE_KEY = 'quietude.microphone'
const PROCESSING_KEY = 'quietude.micProcessing'

function stored(key, fallback = null) {
  try {
    const value = localStorage.getItem(key)
    return value === null ? fallback : value
  } catch {
    return fallback
  }
}

function remember(key, value) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // Private mode, or storage switched off. The setting lasts for this
    // session instead, which is better than refusing to apply it.
  }
}

export function useSpeechEngine() {
  const mode = ref('asleep')          // 'asleep' | 'awake' | 'suppressed'
  const partialText = ref('')         // live preview of what Vosk hears
  const ready = ref(false)
  const error = ref(null)
  const capturing = ref(false)
  const micError = ref(null)
  const devices = ref([])
  const deviceId = ref(stored(DEVICE_KEY, '') || '')
  // The browser's echo cancellation, noise suppression and automatic
  // gain. On by default because the models benefit. Off is here because
  // on some Linux audio stacks the same three are what turn a quiet
  // speaker into silence, and "my microphone works everywhere else"
  // is then a true statement about a broken input.
  const processing = ref(stored(PROCESSING_KEY, '1') !== '0')
  // How many chunks the worklet has handed over, and the state of the
  // audio graph. These separate two failures that look identical from
  // the engine's side: a microphone that is sending silence, and an
  // audio graph that is not running at all. The worklet only produces
  // frames while the context is rendering, and a context renders only
  // when the system has an output device to render to - so a machine
  // with broken or absent audio *output* can stop the microphone
  // working, which is not a sentence anybody guesses on their own.
  const chunksSent = ref(0)
  const contextState = ref('')
  const contextRate = ref(0)
  // Milliseconds of actual speech in the sentence currently being
  // buffered, by either of the engine's two measures. Zero when there
  // is nothing in hand. This is how the state machine above tells a
  // quiet room from somebody who is still talking - see useVoiceChat.
  const speechMs = ref(0)

  // Last-seen counters. Initialised from the first poll rather than
  // zero, so events that happened before this component existed don't
  // all fire at once on mount.
  let seen = null
  let pollTimer = null
  let pollInFlight = null

  const handlers = { wake: null, stop: null, transcript: null, dropped: null }

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
    speechMs.value = data.utterance_speech_ms || 0

    if (seen === null) {
      seen = {
        wake: data.wake_seq,
        stop: data.stop_seq,
        transcript: data.transcript_seq,
        dropped: data.dropped_seq || 0,
      }
      return
    }

    // Compare, then record, then dispatch - so a handler that throws
    // can't cause the same event to fire again on the next poll.
    const firedWake = data.wake_seq > seen.wake
    const firedStop = data.stop_seq > seen.stop
    const firedTranscript = data.transcript_seq > seen.transcript
    // Something was said that did not become a message. Counted the
    // same way as the rest, so a reason that happens twice in a row is
    // reported twice rather than looking like one event.
    const firedDropped = (data.dropped_seq || 0) > (seen.dropped || 0)
    const transcript = data.transcript_text

    seen.wake = data.wake_seq
    seen.stop = data.stop_seq
    seen.transcript = data.transcript_seq
    seen.dropped = data.dropped_seq || 0

    if (firedWake) handlers.wake?.()
    if (firedStop) handlers.stop?.()
    if (firedTranscript && transcript) handlers.transcript?.(transcript)
    if (firedDropped) handlers.dropped?.(data.dropped_reason)
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

  /** Every microphone the system offers. Names need permission, so this
   *  is worth calling again once capture has started. */
  async function listDevices() {
    try {
      const all = await navigator.mediaDevices.enumerateDevices()
      devices.value = all
        .filter((d) => d.kind === 'audioinput')
        .map((d, i) => ({
          id: d.deviceId,
          label: d.label || `Microphone ${i + 1}`,
        }))
    } catch {
      devices.value = []
    }
    return devices.value
  }

  async function startCapture() {
    if (capturing.value) return true
    micError.value = null

    const constraints = {
      channelCount: 1,
      // Ask the browser for the cleanup it's good at. The models
      // benefit and it usually costs nothing - but see `processing`:
      // on some stacks these three are the reason nothing is heard.
      echoCancellation: processing.value,
      noiseSuppression: processing.value,
      autoGainControl: processing.value,
    }
    // Only when one has been chosen. An `exact` constraint on a device
    // that has since been unplugged fails the whole request, and the
    // system default is the right answer for almost everybody.
    if (deviceId.value) constraints.deviceId = { exact: deviceId.value }

    let failure = null
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: constraints, video: false,
      })
    } catch (err) {
      failure = err
      micStream = null
    }

    if (!micStream && deviceId.value && failure?.name !== 'NotAllowedError') {
      // The chosen input is gone - unplugged, or renamed by the system.
      // Fall back to the default rather than refusing to listen at all.
      deviceId.value = ''
      remember(DEVICE_KEY, null)
      delete constraints.deviceId
      try {
        micStream = await navigator.mediaDevices.getUserMedia({
          audio: constraints, video: false,
        })
        failure = null
      } catch (err) {
        failure = err
      }
    }

    if (!micStream) {
      micError.value =
        failure?.name === 'NotAllowedError'
          ? 'I need microphone permission to use voice chat.'
          : "I can't access a microphone - check that one is connected."
      return false
    }

    try {
      // No sample rate asked for.
      //
      // This used to ask for 16kHz, on the grounds that it is what the
      // models want. Browsers mostly ignore it and hand back 48kHz,
      // which is why the worklet resamples from whatever the rate
      // actually turns out to be - so the request was doing nothing on
      // the machines where it worked. On the machines where the engine
      // honours it and the hardware cannot do it, it is the difference
      // between a working context and one that produces silence. The
      // worklet does the conversion either way, so asking for nothing
      // is both simpler and the only version that cannot fail.
      audioContext = new AudioContext()
      const workletUrl = new URL('@/worklets/pcm-processor.js', import.meta.url)
      await audioContext.audioWorklet.addModule(workletUrl)

      micSource = audioContext.createMediaStreamSource(micStream)
      workletNode = new AudioWorkletNode(audioContext, 'pcm-processor')

      workletNode.port.onmessage = (event) => {
        chunksSent.value += 1
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
      contextState.value = audioContext.state
      contextRate.value = Math.round(audioContext.sampleRate || 0)
      capturing.value = true
      // Device names are only given out once permission has been
      // granted, so the list is worth rebuilding now rather than
      // offering "Microphone 1, Microphone 2, Microphone 3".
      listDevices()
      return true
    } catch (err) {
      micError.value = `Couldn't start audio capture: ${err?.message || err}`
      await stopCapture()
      return false
    }
  }

  async function stopCapture() {
    capturing.value = false
    chunksSent.value = 0
    contextState.value = ''
    try { workletNode?.port.postMessage('stop') } catch { /* already gone */ }
    try { workletNode?.disconnect() } catch { /* already gone */ }
    try { micSource?.disconnect() } catch { /* already gone */ }
    // Releasing every track is what actually turns the mic indicator
    // off. Closing the context alone doesn't.
    micStream?.getTracks().forEach((t) => t.stop())
    try { await audioContext?.close() } catch { /* already closed */ }
    workletNode = micSource = micStream = audioContext = null
  }

  // maxMs: how long we expect her to be speaking. The worker treats it
  // as a deadline and starts listening again on its own if nothing
  // lifts suppression - see SUPPRESS_CEILING_MS in speech_engine.py.
  // maxMs: how long we expect her to be speaking. wakeOk: whether her
  // name said over the top of her should cut the phrase short and put
  // her back to listening.
  /** Switch input, or switch the browser's cleanup on and off.
   *  Capture is restarted, because both are fixed when the stream is
   *  opened and neither can be changed on a running one. */
  async function useInput({ id, clean } = {}) {
    if (id !== undefined) {
      deviceId.value = id || ''
      remember(DEVICE_KEY, deviceId.value || null)
    }
    if (clean !== undefined) {
      processing.value = !!clean
      remember(PROCESSING_KEY, processing.value ? '1' : '0')
    }
    if (!capturing.value) return true
    await stopCapture()
    return startCapture()
  }

  const suppress = (maxMs, wakeOk) =>
    api.speechControl('suppress', null, { max_ms: maxMs, wake_ok: !!wakeOk })
      .catch(() => {})
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
    speechMs: readonly(speechMs),
    ready: readonly(ready),
    error: readonly(error),
    capturing: readonly(capturing),
    micError: readonly(micError),
    devices: readonly(devices),
    chunksSent: readonly(chunksSent),
    contextState: readonly(contextState),
    contextRate: readonly(contextRate),
    deviceId: readonly(deviceId),
    processing: readonly(processing),
    listDevices, useInput,
    on, startPolling, stopPolling, startCapture, stopCapture,
    suppress, unsuppress, sleep, stop, lastWakeSeq,
  }
}
