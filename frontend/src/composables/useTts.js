/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * useTts.js
 * Quietude's voice, played in the browser.
 *
 * This is where the audio-pipeline decision pays off. In the Windows
 * build the backend owned a pygame mixer, so play/pause/resume/stop were
 * four HTTP routes driving a worker process, and the UI polled a status
 * endpoint every 400ms to find out what playback was doing - with a
 * guard for the case where "idle" meant "still synthesizing" rather than
 * "finished", because the two were indistinguishable from outside.
 *
 * Now the backend synthesizes a WAV and returns its URL. Playback is one
 * <audio> element:
 *
 *   pause/resume/stop  properties of the element, instantly and exactly
 *   "has it finished"  the 'ended' event, rather than inferred polling
 *   volume             a property, so changing it needs no re-synthesis
 *   seeking            free, if it's ever wanted
 *
 * The polling loop, the synthesizing-vs-finished ambiguity, and three of
 * the four routes all disappeared.
 */

import { computed, onScopeDispose, readonly, ref } from 'vue'
import { api } from '@/api/client'

export function useTts() {
  const speaking = ref(false)
  const paused = ref(false)
  const preview = ref('')      // what's playing, for the playback bar
  const available = ref(false)
  const lastError = ref(null)

  let audio = null
  // Resolved when the current phrase finishes, however it finishes -
  // naturally, interrupted, or failed. Callers await this to sequence
  // "say this, then start listening".
  let finished = null

  const active = computed(() => speaking.value || paused.value)

  function teardown() {
    if (!audio) return
    audio.onended = audio.onerror = null
    audio.pause()
    // Dropping the src releases the decoded buffer; without it a long
    // session accumulates one per phrase.
    audio.src = ''
    audio = null
  }

  function settle() {
    speaking.value = false
    paused.value = false
    preview.value = ''
    teardown()
    finished?.resolve()
    finished = null
  }

  /**
   * Speaks a phrase and resolves once it has genuinely finished playing.
   *
   * `overrides` optionally carries rate, volume and voice_id for this
   * phrase alone, which is what lets the settings page preview the
   * controls as they currently read instead of the last saved values.
   * Nothing is written to disk by speaking.
   * Resolves rather than rejects on failure: a phrase that couldn't be
   * spoken shouldn't break the caller's sequence - the chat bubble is
   * already on screen and is the thing that actually matters.
   */
  async function speak(text, overrides) {
    if (!text?.trim()) return
    stop() // a new phrase always wins over whatever is playing

    let payload
    try {
      payload = await api.ttsSpeak(text, overrides)
    } catch (err) {
      lastError.value = err?.message || 'Text-to-speech is unavailable.'
      available.value = false
      return
    }

    available.value = true
    lastError.value = null

    let resolve
    const promise = new Promise((r) => { resolve = r })
    finished = { resolve, promise }

    audio = new Audio(payload.url)
    audio.volume = Math.max(0, Math.min(1, payload.volume ?? 1))
    audio.onended = settle
    audio.onerror = () => {
      lastError.value = "I couldn't play that audio."
      settle()
    }

    preview.value = text
    speaking.value = true
    paused.value = false

    try {
      await audio.play()
    } catch {
      // Autoplay policy, or the element was torn down mid-call. Either
      // way this phrase isn't going to be heard; don't leave a caller
      // awaiting it forever.
      settle()
      return
    }

    return promise
  }

  /**
   * Applies a volume change to whatever is playing right now.
   *
   * Volume is a property of the audio element rather than something
   * baked into the WAV, so the settings page's slider can be heard
   * while a preview is still mid-sentence - no re-synthesis, no round
   * trip. That is most of what makes the preview feel live.
   */
  function setVolume(value) {
    const v = Number(value)
    if (!Number.isFinite(v)) return
    if (audio) audio.volume = Math.max(0, Math.min(1, v))
  }

  function pause() {
    if (!audio || !speaking.value) return
    audio.pause()
    speaking.value = false
    paused.value = true
  }

  async function resume() {
    if (!audio || !paused.value) return
    try {
      await audio.play()
      speaking.value = true
      paused.value = false
    } catch {
      settle()
    }
  }

  /** The "stop talking" interrupt, and the playback bar's stop button. */
  function stop() {
    // Not gated on `audio` existing. If the element is already gone but
    // the flags still say speaking, this is the only thing that can
    // clear them - and an early return here would leave a playback bar
    // on screen whose buttons do nothing.
    settle()
  }

  async function refreshStatus() {
    try {
      const data = await api.ttsStatus()
      available.value = !!data.ready
      lastError.value = data.error
      return data
    } catch {
      available.value = false
      return null
    }
  }

  // settle(), not teardown(): this is a shared singleton whose scope
  // belongs to whichever component happened to call it first, so it is
  // disposed when that component unmounts - navigating from the console
  // to /commands mid-sentence, say. teardown() alone would destroy the
  // audio element while leaving speaking/preview set, so coming back
  // would show a playback bar for audio that no longer exists, with
  // dead controls. It would also leave anything awaiting the current
  // phrase (sayAloud) waiting forever, since onended is cleared.
  onScopeDispose(() => { settle() })

  return {
    speaking: readonly(speaking),
    paused: readonly(paused),
    preview: readonly(preview),
    available: readonly(available),
    lastError: readonly(lastError),
    active,
    speak, pause, resume, stop, setVolume, refreshStatus,
  }
}

// One instance shared across the app: the playback bar in the top bar,
// the speaker icon on each bubble, and the voice-chat state machine all
// have to agree about what is currently playing, and "a new phrase stops
// the previous one" only holds if they share an element.
let shared = null
export function useSharedTts() {
  if (!shared) shared = useTts()
  return shared
}
