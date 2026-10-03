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
import { useIdentityStore } from '@/stores/identity'
import { wait } from '@/composables/useMessageLog'

// How long the finished transcription sits in the message box before it
// is sent. Long enough to read a short command and see that it was heard
// correctly; short enough that it doesn't feel like a delay.
const SETTLED_DWELL_MS = 700

// No speech for this long while awake -> "did you want to say something?"
//
// Measured from the last thing she heard, and the engine now waits
// three seconds of silence before deciding an utterance is over - so
// this has to be comfortably longer than that, or she interrupts the
// pause she is supposed to be waiting through.
const AWAKE_CHECKIN_SILENCE_MS = 12_000
// Still nothing this much longer after that -> back to sleep.
const AWAKE_SLEEP_SILENCE_MS = 8_000

// Longest she will wait for a phrase to finish playing before she
// starts listening again regardless.
//
// The recogniser is switched off while she speaks, and it used to be
// switched back on by a promise resolving when the audio ended. A
// promise that never resolves - playback paused from the bar, a clip
// that stalls, an element torn down between the request and the first
// frame - left her deaf, and not for a moment: for the rest of the
// session. She went on answering into a microphone that had been
// turned off, which from outside is "she stopped hearing me" with
// nothing on screen to say so.
//
// Roughly ten characters a second, which is slower than she actually
// talks, plus a few seconds of slack. It is a ceiling and not a
// schedule: normally the audio ends and she is listening again long
// before this. The engine keeps the same deadline independently, so
// this failing too is still bounded - see SUPPRESS_CEILING_MS.
const SPEAK_CEILING_MIN_MS = 4_000
const SPEAK_CEILING_MAX_MS = 120_000

function speakCeilingMs(text) {
  const estimate = ((text || '').length / 10) * 1000 + 3_000
  return Math.round(
    Math.max(SPEAK_CEILING_MIN_MS, Math.min(SPEAK_CEILING_MAX_MS, estimate)),
  )
}

export function useVoiceChat({ sendMessage, addQuietude }) {
  const engine = useSpeechEngine()
  const tts = useSharedTts()
  const identity = useIdentityStore()
  const { wakeTone, sleepTone } = useChimes()

  /**
   * Whether her name should cut this phrase short if somebody says it.
   *
   * Almost always yes - interrupting her is how you get a word in
   * while she reads out a long answer. Not when the phrase contains
   * her name, because then she says it herself and wakes herself up:
   * "say Hello Ada to wake me" is a sentence she really does speak.
   */
  function wakeMayInterrupt(text) {
    const name = (identity.name || '').trim().toLowerCase()
    if (!name) return false
    return !(text || '').toLowerCase().includes(name)
  }

  const state = ref('off')       // 'off' | 'asleep' | 'awake'
  // The finished transcription, shown in the message box for a beat
  // before it is sent. Empty at all other times.
  const settled = ref('')
  const starting = ref(false)
  const notice = ref(null)       // mic permission / hardware problems

  let silenceTimer = null
  let lastActivity = 0
  let checkinDone = false
  let checkinInFlight = false

  // Transcriptions waiting their turn.
  //
  // Handling one runs the whole round trip and then waits for her to
  // finish speaking the answer, which is seconds. A second sentence
  // finished in that time used to arrive while the first was still
  // being dealt with, and the two trod on each other - the message box
  // showed one and sent the other. They queue now. Nothing said is
  // dropped because she was busy answering the thing before it.
  const pending = []
  let draining = false
  // Which phrase is currently being spoken. See sayAloud.
  let speakSeq = 0

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
    const mine = ++speakSeq
    const ceiling = speakCeilingMs(text)
    const suppressed = engine.suppress(ceiling, wakeMayInterrupt(text))
    try {
      // Raced against the ceiling rather than simply awaited. See
      // SPEAK_CEILING_MIN_MS: the whole of her hearing hung off this
      // one promise, and a promise is not a guarantee that anything
      // will happen.
      await Promise.all([suppressed, Promise.race([tts.speak(text), wait(ceiling)])])
    } finally {
      // Only the most recent phrase lifts suppression. A newer one
      // starting stops the previous clip, which resolves the older
      // wait - and that older wait going on to unsuppress would switch
      // the microphone back on in the middle of the new phrase, so she
      // would hear herself and transcribe it as a command.
      if (mine === speakSeq) await engine.unsuppress()
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

    // She is demonstrably hearing someone: the engine has a sentence in
    // hand with speech in it. Both of the things below - asking "did
    // you want to say something?" and going back to sleep - are
    // answers to silence, and neither is an answer to this.
    //
    // Without it, the only thing that counted as activity was a
    // finished transcription. So the clock ran while somebody was
    // talking, and if the gate did not find its three seconds of quiet
    // in time - a fan, a noisy mic, a long sentence - she interrupted
    // at twelve seconds and went to sleep at twenty, discarding every
    // word of it. From the outside: she stops sending what you say.
    if (engine.speechMs.value > 0) {
      noteActivity()
      return
    }

    // Nor while she is speaking. The recogniser is suppressed for the
    // length of it, so that stretch is guaranteed to look like silence
    // and is not.
    if (engine.mode.value === 'suppressed' || draining || pending.length) {
      noteActivity()
      return
    }

    const idle = Date.now() - lastActivity

    if (!checkinDone && idle >= AWAKE_CHECKIN_SILENCE_MS) {
      checkinInFlight = true
      checkinDone = true
      // Written, not spoken, for the same reason as the wake
      // acknowledgement: she asks this precisely when someone is about
      // to speak, and saying it aloud would deafen her through their
      // answer.
      addQuietude('Did you want to say something?')
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

    // She acknowledges in writing and with the tone, and does not say it
    // aloud.
    //
    // She used to. Speaking it meant suppressing the recogniser for the
    // whole phrase, because otherwise she hears herself - and that is
    // around two seconds of being deaf, starting at the exact moment the
    // wake word lands. Which is the moment people talk: you say her
    // name and the command in one breath. Everything said in that window
    // went nowhere, so she would wait, ask "did you want to say
    // something?", and go back to sleep, having never heard a word of
    // it. The tone already says she is awake, and it costs no listening
    // time to play.
    addQuietude("Yes, I'm listening.")

    // Listening starts now, not after a phrase has finished playing.
    noteActivity()
    silenceTimer = setInterval(checkAwakeSilence, 1000)
  }

  /**
   * A finished transcription. Queued, then handled one at a time.
   *
   * The handling is slow by design - the box holds the words for a beat,
   * the command goes to the backend, and she speaks the answer - so
   * sentences can and do finish faster than they are dealt with. Each
   * one still gets sent, in the order it was said.
   */
  function onTranscript(text) {
    // 'off' only, not 'not awake'.
    //
    // This said `!== 'awake'` and silently dropped everything else,
    // which meant the interface could throw away a sentence the engine
    // had just spent two seconds transcribing. It happens exactly when
    // it hurts most: the interface decides the session has lapsed and
    // asks the engine to sleep, the engine finishes the sentence it was
    // holding and hands it over - and it lands a few hundred
    // milliseconds after `state` became 'asleep', so it went nowhere.
    // The engine is the one that knows whether it was listening. If it
    // produced a transcription, somebody said something, and the only
    // honest thing to do with it is send it.
    if (state.value === 'off' || !text) return
    noteActivity()
    pending.push(text)
    drain()
  }

  async function drain() {
    if (draining) return
    draining = true
    try {
      while (pending.length && state.value !== 'off') {
        await handleTranscript(pending.shift())
      }
    } finally {
      draining = false
    }
  }

  async function handleTranscript(text) {
    noteActivity()
    clearSilenceTimer()
    // A sentence finished after she had already lapsed is still a
    // sentence. Sending it means answering it, which means she is in
    // the conversation again - so she is awake again too.
    if (state.value === 'asleep') {
      state.value = 'awake'
      if (!silenceTimer) silenceTimer = setInterval(checkAwakeSilence, 1000)
    }

    // What was finally heard goes into the message box first, and stays
    // there for a beat before it is sent.
    //
    // The live preview above it comes from the wake-word model, which is
    // the small streaming one - on a short command it can finish the
    // whole utterance before producing a single partial, so the box sits
    // empty while you talk and the message simply appears, which reads
    // as the words having gone somewhere else. This is also the only
    // point where the box can show the accurate transcription rather
    // than the rough one, so it is worth a moment either way.
    settled.value = text
    await wait(SETTLED_DWELL_MS)
    settled.value = ''

    await sendMessage(text, { spoken: true })

    // She stays awake and listens for the next thing.
    //
    // She used to go straight back to sleep, so every single sentence
    // needed the wake word in front of it - which is fine for one
    // command and exhausting for a conversation. Now one wake opens a
    // window, and the window stays open as long as you keep talking.
    //
    // It closes on its own: checkAwakeSilence asks after
    // AWAKE_CHECKIN_SILENCE_MS of nothing and sleeps after that. So she
    // is not left listening to the room indefinitely because somebody
    // said her name once - the wake word still decides when she starts,
    // it just no longer decides when she stops.
    if (state.value === 'awake') {
      noteActivity()
      if (!silenceTimer) silenceTimer = setInterval(checkAwakeSilence, 1000)
    }
  }

  function onStop() {
    // "stop talking", heard while she was speaking.
    tts.stop()
  }

  /**
   * Something was said and did not become a message.
   *
   * Until this, every one of those was silence. Transcription raising,
   * the model returning nothing recognisable, an utterance arriving
   * with no speech in it - all of them ended in `return`, and what the
   * user saw was an assistant that had stopped answering with no
   * indication that anything had happened at all. Which is the single
   * hardest thing to report, because there is nothing to report.
   *
   * It says so now. Noise and coughs are filtered out by the engine
   * before they get here, so this only fires when somebody really did
   * say something to her.
   */
  function onDropped(reason) {
    if (state.value === 'off' || !reason) return
    noteActivity()
    addQuietude(reason, 'warning')
  }

  engine.on('wake', onWake)
  engine.on('transcript', onTranscript)
  engine.on('stop', onStop)
  engine.on('dropped', onDropped)

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
    pending.length = 0
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
    settled: readonly(settled),
    engineMode: engine.mode,
    // Handed out so the voice check can act on the session that is
    // actually running - switching the microphone on a second engine
    // would change nothing anybody could hear.
    engine,
    start, stop, sayAloud,
  }
}
