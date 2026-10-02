/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * useChimes.js
 * The startup / shutdown / reset chimes, and the little wake and sleep
 * tones.
 *
 * The mp3s carry over from the Windows build unchanged. Their durations
 * are measured constants rather than guesses: the boot and reset
 * animations are timed to them so sound and visuals finish together,
 * which is also why the shutdown grace period in the backend is what it
 * is.
 */

import { onScopeDispose } from 'vue'
import startupUrl from '@/assets/sounds/startup.mp3'
import shutdownUrl from '@/assets/sounds/shutdown.mp3'
import resetUrl from '@/assets/sounds/reset.mp3'

export const STARTUP_SOUND_MS = 5050
export const SHUTDOWN_SOUND_MS = 4200
export const RESET_SOUND_MS = 4900

const CHIMES = { startup: startupUrl, shutdown: shutdownUrl, reset: resetUrl }
const DURATIONS = {
  startup: STARTUP_SOUND_MS,
  shutdown: SHUTDOWN_SOUND_MS,
  reset: RESET_SOUND_MS,
}

let audioContext = null

function context() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)()
  }
  // A context created before the first user gesture starts suspended;
  // resuming on use is what makes the wake tone audible after the user
  // has clicked anything at all.
  if (audioContext.state === 'suspended') audioContext.resume().catch(() => {})
  return audioContext
}

export function useChimes() {
  const playing = new Set()

  function play(name) {
    const url = CHIMES[name]
    if (!url) return null
    const audio = new Audio(url)
    playing.add(audio)
    audio.onended = () => playing.delete(audio)
    // Chimes are decoration. A blocked autoplay must never break a flow
    // that's waiting on the animation beside it.
    audio.play().catch(() => playing.delete(audio))
    return audio
  }

  /**
   * Plays a chime and resolves when it has genuinely finished - the
   * 'ended' event, not a guessed duration.
   *
   * The measured lengths above are still used as a ceiling, because a
   * chime that never starts (autoplay blocked, no audio device) would
   * otherwise never resolve and would hang whatever is waiting on it.
   * So: ended if it plays, the known length plus a margin if it
   * doesn't.
   */
  function playUntilDone(name, { timeoutMs = null } = {}) {
    const audio = play(name)
    const ceiling = timeoutMs ?? (DURATIONS[name] ?? 5000) + 600
    if (!audio) return new Promise((resolve) => setTimeout(resolve, 150))

    return new Promise((resolve) => {
      let settled = false
      const done = () => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        resolve()
      }
      const timer = setTimeout(done, ceiling)
      audio.addEventListener('ended', done, { once: true })
      audio.addEventListener('error', done, { once: true })
    })
  }

  function stopAll() {
    playing.forEach((a) => { a.pause(); a.src = '' })
    playing.clear()
  }

  /** A short synthesized tone - cheaper than shipping another asset. */
  function beep(freq = 880, durationMs = 140, delayMs = 0) {
    setTimeout(() => {
      try {
        const ctx = context()
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.frequency.value = freq
        osc.type = 'sine'
        // Ramp down rather than cutting off: an abrupt stop on a sine
        // wave is an audible click.
        gain.gain.setValueAtTime(0.08, ctx.currentTime)
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + durationMs / 1000)
        osc.connect(gain).connect(ctx.destination)
        osc.start()
        osc.stop(ctx.currentTime + durationMs / 1000)
      } catch { /* no audio output - not worth surfacing */ }
    }, delayMs)
  }

  /** Rising chirp - "I'm listening". */
  const wakeTone = () => { beep(720, 90); beep(1080, 110, 100) }
  /** Falling tone - "going back to sleep". */
  const sleepTone = () => { beep(720, 110); beep(420, 160, 110) }

  onScopeDispose(stopAll)

  return { play, playUntilDone, stopAll, beep, wakeTone, sleepTone }
}
