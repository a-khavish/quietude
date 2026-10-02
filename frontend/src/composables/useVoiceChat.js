/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * useVoiceChat.js
 * The asleep / awake state machine for hands-free use.
 *
 * Same behaviour as the Windows build, driven by local models instead of
 * the Web Speech API:
 *
 *   off     not listening at all. The chat box works normally.
 *   asleep  always listening, but only for "Hello Quietude". Nothing is
 *           treated as a command and nothing is transcribed. The chat
 *           box is locked and blurred, with a live red preview of what
 *           Vosk is picking up - so you can see she's hearing sound
 *           without her acting on it.
 *   awake    the wake phrase was heard. Speech is buffered until a pause,
 *           then transcribed in one go and sent as a command - never
 *           word by word. Quiet for a while and she checks in once;
 *           quiet after that and she goes back to sleep with a tone.
 *
 * Two things the local pipeline makes genuinely better:
 *
 *   The reference had to restart the Web Speech recognizer constantly -
 *   it ends on its own every few seconds, and onend scheduled a restart
 *   80ms later with guards against overlapping start() calls, because
 *   every millisecond in that gap was audio simply never heard. The
 *   local engine doesn't stop, so all of that is gone.
 *
 *   "Ignore what you hear while Quietude is speaking" was a frontend
 *   concern there - a ttsSpeaking flag that onresult checked, so her own
 *   voice still reached the recognizer and was filtered after the fact.
 *   Now suppression happens in the engine: the recognizer is told to
 *   check only for the interrupt phrase, so her voice is never
 *   transcribed at all.
 */

import { onScopeDispose, readonly, ref } from 'vue'
import { useSpeechEngine } from '@/composables/useSpeechEngine'
import { useSharedTts } from '@/composables/useTts'
import { useChimes } from '@/composables/useChimes'

// No speech for this long while awake -> "did you want to say something?"
const AWAKE_CHECKIN_SILENCE_MS = 10_000
// Still nothing this much longer after that -> back to sleep.
const AWAKE_SLEEP_SILENCE_MS = 6_000

export function useVoiceChat({ sendMessage, addQuietude }) {
  const engine = useSpeechEngine()
  const tts = useSharedTts()
  const { wakeTone, sleepTone } = useChimes()

  const state = ref('off')       // 'off' | 'asleep' | 'awake'
  const starting = ref(false)
  const notice = ref(null)       // mic permission / hardware problems

  let silenceTimer = null
  let lastActivity = 0
  let checkinDone = false
  let checkinInFlight = false

  /**
   * Speaks a phrase with the recognizer suppressed for its duration, so
   * the mic can't hear her own voice - except for the interrupt phrase,
   * which the engine still watches for. This wrapper is the single place
   * suppression is paired with playback, so the two can't drift apart.
   */
  async function sayAloud(text) {
    // Suppression and synthesis are kicked off together rather than in
    // sequence. They're independent - one tells the recognizer to stop
    // listening for commands, the other asks for audio - and doing them
    // one after the other charged every spoken line an extra round trip
    // before any sound could start.
    //
    // Ordering is still safe: the clip cannot play before the POST that
    // requests it returns, and suppression is requested first, so the
    // recognizer is told to stand down no later than the audio begins.
    const suppressed = engine.suppress()
    try {
      await Promise.all([suppressed, tts.speak(text)])
    } finally {
      // Always unsuppress, even if playback failed - otherwise a failed
      // phrase leaves the engine deaf to everything but "stop talking".
      await engine.unsuppress()
    }
  }

  function clearSilenceTimer() {
    if (silenceTimer) clearInterval(silenceTimer)
    silenceTimer = null
  }

  function noteActivity() {
    lastActivity = Date.now()
    checkinDone = false
  }

  async function goToSleep({ playTone = true } = {}) {
    if (state.value === 'off') return
    clearSilenceTimer()
    state.value = 'asleep'
    // Carries the wake counter, so if she woke again in the moment
    // between this timer firing and the request landing, the backend
    // drops it rather than ending the new session.
    await engine.sleep()
    if (playTone) sleepTone()
  }

  async function checkAwakeSilence() {
    if (state.value !== 'awake' || checkinInFlight) return
    const idle = Date.now() - lastActivity

    if (!checkinDone && idle >= AWAKE_CHECKIN_SILENCE_MS) {
      checkinInFlight = true
      checkinDone = true
      const line = 'Did you want to say something?'
      addQuietude(line)
      await sayAloud(line)
      // The fresh window starts when she finishes asking, not when the
      // timer fired - otherwise she asks and immediately gives up.
      if (state.value === 'awake') lastActivity = Date.now()
      checkinInFlight = false
    } else if (checkinDone && idle >= AWAKE_CHECKIN_SILENCE_MS + AWAKE_SLEEP_SILENCE_MS) {
      await goToSleep({ playTone: true })
    }
  }

  async function onWake() {
    if (state.value === 'off') return
    state.value = 'awake'
    clearSilenceTimer()
    wakeTone()

    const line = "Yes, I'm listening."
    addQuietude(line)
    await sayAloud(line)

    if (state.value !== 'awake') return // turned off while she was talking

    // The "still awake?" clock starts once she's actually done talking
    // and really listening - not from when the wake word landed.
    noteActivity()
    silenceTimer = setInterval(checkAwakeSilence, 1000)
  }

  async function onTranscript(text) {
    if (state.value !== 'awake') return
    noteActivity()
    clearSilenceTimer()
    await sendMessage(text, { spoken: true })
    // The command has been handled; go back to waiting for the wake
    // phrase rather than staying open for a follow-up, which is what the
    // reference did and keeps the "nothing is heard unless you woke her"
    // guarantee easy to reason about.
    if (state.value === 'awake') await goToSleep({ playTone: false })
  }

  function onStop() {
    // "stop talking", heard while she was speaking.
    tts.stop()
  }

  engine.on('wake', onWake)
  engine.on('transcript', onTranscript)
  engine.on('stop', onStop)

  async function start() {
    if (state.value !== 'off' || starting.value) return false
    starting.value = true
    notice.value = null
    try {
      const got = await engine.startCapture()
      if (!got) {
        notice.value = engine.micError.value
        return false
      }
      engine.startPolling()
      await engine.sleep()
      state.value = 'asleep'
      return true
    } finally {
      starting.value = false
    }
  }

  async function stop() {
    clearSilenceTimer()
    state.value = 'off'
    checkinDone = false
    checkinInFlight = false
    tts.stop()
    await engine.unsuppress()
    await engine.stop()
  }

  onScopeDispose(() => { stop() })

  return {
    state: readonly(state),
    starting: readonly(starting),
    notice,
    partialText: engine.partialText,
    engineMode: engine.mode,
    start, stop, sayAloud,
  }
}
