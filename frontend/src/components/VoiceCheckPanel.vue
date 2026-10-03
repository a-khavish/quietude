<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  VoiceCheckPanel.vue
  Why she can't hear you.

  Hands-free has one failure mode and it is silence. You say something
  and nothing happens. Every stage between the microphone and the
  message box can fail that way - no permission, no audio arriving, a
  gate sitting above your voice, a gate sitting under the room so the
  pause never arrives, a model that heard nothing it could use - and
  from outside all of them look identical, which is to say they look
  like nothing at all.

  The engine has published the numbers to tell them apart from the
  beginning. There was simply nowhere that showed them. A comment in
  speech_engine.py says the level is "the difference between 'she isn't
  hearing me' being a mystery and being a number you can look at", and
  it had been a mystery for every release since, because the meter it
  describes was never built.

  So this is the meter, and the rest of the chain beside it, in the
  order the sound travels. Each stage says what it is doing rather than
  whether it is fine, because "fine" is what everything says right up
  until you try to use it.

  A panel over the console rather than a page of its own, and that is
  not a layout preference. Every other screen here is a route, and
  leaving the console unmounts it - which disposes the voice session and
  switches the microphone off. So the one screen whose entire purpose is
  to be read *while* you are talking to her was the one screen that
  turned off the thing it was measuring. It opens over the console now,
  with the chat and the meter both on screen and nothing torn down.
-->
<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { useIdentityStore } from '@/stores/identity'

const props = defineProps({
  /** The live speech engine, so the microphone picker acts on the
   *  session that is actually running rather than a second one. */
  engine: { type: Object, required: true },
  voiceOn: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const identity = useIdentityStore()

const status = ref(null)
const error = ref(null)
const saving = ref(false)

// Peak level seen since the page opened. A level that never rises is
// the single most useful fact here, and a reading taken between two
// words looks exactly like a microphone that isn't working.
const peak = ref(0)
const heardSpeech = ref(false)

let timer = null

async function tick() {
  try {
    const data = await api.speechStatus()
    status.value = data
    error.value = null
    peak.value = Math.max(peak.value, data.level || 0)
    if (data.voiced) heardSpeech.value = true
  } catch (err) {
    error.value = err.message
  }
}

function onKey(event) {
  if (event.key === 'Escape') emit('close')
}

onMounted(async () => {
  await tick()
  timer = setInterval(tick, 200)
  props.engine.listDevices()
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  window.removeEventListener('keydown', onKey)
})

const switching = ref(false)

async function chooseInput(event) {
  switching.value = true
  peak.value = 0
  heardSpeech.value = false
  try {
    await props.engine.useInput({ id: event.target.value })
  } finally {
    switching.value = false
  }
}

async function toggleProcessing(event) {
  switching.value = true
  peak.value = 0
  heardSpeech.value = false
  try {
    await props.engine.useInput({ clean: event.target.checked })
  } finally {
    switching.value = false
  }
}

const s = computed(() => status.value || {})

/** Where the bar sits, with the gate at the middle so both sides read. */
function scale(value) {
  const gate = s.value.vad_threshold || 1
  // A log-ish curve: speech is many times the gate and a linear bar
  // would pin at the top the moment anyone spoke.
  const ratio = Math.max(0, (value || 0)) / gate
  const pos = ratio <= 1 ? ratio * 50 : 50 + Math.min(50, Math.log2(ratio) * 16)
  return Math.max(0, Math.min(100, pos))
}

const stages = computed(() => {
  const d = s.value
  const ready = d.ready
  const sending = props.engine.chunksSent.value > 0
  const arriving = peak.value > 1
  return [
    {
      key: 'models',
      name: 'The models',
      ok: ready,
      detail: ready
        ? 'Wake word and transcription are both loaded.'
        : (d.error || 'Still loading, or not downloaded yet.'),
      fix: ready ? '' : 'Say "speech models" to see what is missing.',
    },
    {
      key: 'frames',
      name: 'The audio engine',
      ok: sending,
      detail: sending
        ? `Sending audio — ${props.engine.chunksSent.value} pieces so far, `
          + `at ${props.engine.contextRate.value} Hz.`
        : (props.voiceOn
            ? 'The audio graph is not producing anything.'
            : 'Not started — turn voice chat on.'),
      // Worth saying out loud, because nobody arrives at it by
      // reasoning: a browser renders audio only while it has somewhere
      // to render it, so broken *output* can stop the microphone.
      fix: sending || !props.voiceOn
        ? ''
        : 'The browser renders audio only while it has a working output '
          + 'device. If this machine has no sound output, or it is broken, '
          + 'the microphone stops with it.',
    },
    {
      key: 'mic',
      name: 'Sound in what it sends',
      ok: arriving,
      detail: arriving
        ? `Yes. Loudest so far: ${Math.round(peak.value)}.`
        : 'Everything arriving is silence.',
      fix: arriving || !sending
        ? ''
        : 'The input is open but there is nothing in it. Try another '
          + 'microphone below, or turn off the cleanup — on some setups it '
          + 'is what removes the whole signal.',
    },
    {
      key: 'gate',
      name: 'Hearing you over the room',
      ok: heardSpeech.value,
      detail: heardSpeech.value
        ? 'Your voice has got above the room at least once.'
        : 'Nothing has counted as speech yet.',
      fix: heardSpeech.value
        ? ''
        : 'If the bar below moves when you talk but never passes the mark, '
          + 'raise the sensitivity.',
    },
    {
      key: 'wake',
      name: 'The wake word',
      ok: (d.wake_seq || 0) > 0,
      detail: (d.wake_seq || 0) > 0
        ? `Heard ${d.wake_seq} time${d.wake_seq === 1 ? '' : 's'} this session.`
        : `Nothing yet. Say "${d.wake_phrase || identity.wakePhrase}".`,
      fix: '',
    },
    {
      key: 'transcript',
      name: 'Turning it into words',
      ok: (d.transcript_seq || 0) > 0,
      detail: (d.transcript_seq || 0) > 0
        ? `${d.transcript_seq} sentence${d.transcript_seq === 1 ? '' : 's'} transcribed.`
        : 'Nothing has been transcribed yet.',
      fix: (d.dropped_seq || 0) > 0 ? d.dropped_reason : '',
    },
  ]
})

const modeLabel = computed(() => ({
  asleep: 'Listening for her name',
  awake: 'Listening to you',
  suppressed: 'Speaking - not listening',
}[s.value.mode] || s.value.mode || '—'))

async function setSensitivity(event) {
  const level = Number(event.target.value)
  saving.value = true
  try {
    await api.speechSensitivity(level)
    // The gate is re-measured from scratch when this changes, so the
    // old peak says nothing about the new setting.
    peak.value = 0
    heardSpeech.value = false
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="vc-backdrop" @click.self="emit('close')">
    <section class="vc-panel" role="dialog" aria-label="Voice check">
      <header class="vc-head">
        <div>
          <span class="eyebrow">VOICE CHECK</span>
          <h2 class="vc-title">CAN SHE HEAR YOU</h2>
        </div>
        <button type="button" class="vc-close" title="Close" @click="emit('close')">
          ×
        </button>
      </header>

      <p class="vc-intro">
        Voice chat keeps running while this is open — talk, and watch. Each line
        below is one step between your microphone and the message box, in order.
        The first one that stays dark is the one to fix.
      </p>

      <p v-if="error" class="vc-engine-error">{{ error }}</p>

        <!-- the meter -->
        <section class="vc-meter-block">
          <div class="vc-meter-head">
            <span class="vc-meter-title">What she's hearing</span>
            <span class="vc-mode" :class="s.mode">{{ modeLabel }}</span>
          </div>

          <div class="vc-meter" :class="{ voiced: s.voiced }">
            <div class="vc-meter-fill" :style="{ width: scale(s.level) + '%' }" />
            <div class="vc-meter-peak" :style="{ left: scale(peak) + '%' }" />
            <!-- The gate sits at the midpoint by construction, so "past
                 halfway" means "counts as speech" without reading numbers. -->
            <div class="vc-meter-gate" />
          </div>

          <div class="vc-meter-legend">
            <span>room {{ Math.round(s.noise_floor || 0) }}</span>
            <span class="vc-gate-label">speech starts here</span>
            <span>now {{ Math.round(s.level || 0) }}</span>
          </div>

          <label class="vc-sensitivity">
            <span class="vc-label">MICROPHONE</span>
            <select
              class="vc-select"
              :value="engine.deviceId.value"
              :disabled="switching"
              @change="chooseInput"
            >
              <option value="">System default</option>
              <option v-for="d in engine.devices.value" :key="d.id" :value="d.id">
                {{ d.label }}
              </option>
            </select>
            <span class="vc-hint">
              If nothing reaches her, it is usually this: the default input is a
              webcam, a monitor, or a device that is muted.
            </span>
          </label>

          <label class="vc-check">
            <input
              type="checkbox"
              :checked="engine.processing.value"
              :disabled="switching"
              @change="toggleProcessing"
            />
            <span>
              Let the browser clean up the sound
              <span class="vc-hint-inline">
                — echo cancellation, noise suppression and automatic gain. Usually
                helps. On some Linux audio setups it is what turns a quiet speaker
                into silence, so turn it off if the bar never moves.
              </span>
            </span>
          </label>

          <label class="vc-sensitivity">
            <span class="vc-label">
              SENSITIVITY — how much louder than the room your voice has to be
            </span>
            <input
              type="range"
              :min="s.sensitivity_min || 1"
              :max="s.sensitivity_max || 10"
              :value="s.sensitivity || 5"
              :disabled="saving"
              @change="setSensitivity"
            />
            <span class="vc-hint">
              Raise it if the bar moves when you talk but never reaches the mark.
              Lower it if the bar sits past the mark when the room is quiet — that
              means she never hears a pause, so your sentence never ends.
            </span>
          </label>
        </section>

        <!-- the chain -->
        <ol class="vc-stages">
          <li
            v-for="(stage, index) in stages"
            :key="stage.key"
            class="vc-stage"
            :class="{ ok: stage.ok }"
          >
            <span class="vc-stage-number">{{ index + 1 }}.</span>
            <div class="vc-stage-body">
              <p class="vc-stage-name">{{ stage.name }}</p>
              <p class="vc-stage-detail">{{ stage.detail }}</p>
              <p v-if="stage.fix" class="vc-stage-fix">{{ stage.fix }}</p>
            </div>
            <span class="vc-stage-mark">{{ stage.ok ? '✓' : '·' }}</span>
          </li>
        </ol>

        <!-- the sentence in flight -->
        <section v-if="s.mode === 'awake'" class="vc-utterance">
          <p class="vc-label">THE SENTENCE SHE'S HOLDING</p>
          <p class="vc-utterance-line">
            {{ Math.round((s.utterance_ms || 0) / 100) / 10 }}s recorded,
            {{ Math.round((s.utterance_speech_ms || 0) / 100) / 10 }}s of it speech,
            {{ Math.round((s.utterance_silence_ms || 0) / 100) / 10 }}s of quiet since
            you last said anything.
          </p>
          <p class="vc-hint">
            She sends it after
            {{ Math.round((s.end_of_utterance_ms || 3000) / 1000) }}s of quiet.
            If that number never climbs while you're not talking, the room is
            louder than the gate — lower the sensitivity.
          </p>
        </section>

        <p v-if="s.error" class="vc-engine-error">{{ s.error }}</p>

      <p class="vc-where">
        Nothing on this page leaves the machine, and nothing is recorded — these
        are the numbers the engine is already working from.
      </p>
    </section>
  </div>
</template>

<style scoped>
.vc-backdrop {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 24px 16px;
  overflow: auto;
  background: rgba(2, 10, 14, 0.78);
  backdrop-filter: blur(3px);
  animation: vcFade 0.18s ease;
}

@keyframes vcFade {
  from { opacity: 0; }
  to { opacity: 1; }
}

.vc-panel {
  width: 100%;
  max-width: 680px;
  margin: auto;
  padding: 22px 26px 26px;
  border: 1px solid var(--cyan);
  border-radius: 10px;
  background: #06161b;
  box-shadow: 0 0 60px rgba(45, 226, 230, 0.18);
}

.vc-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.vc-title {
  margin: 4px 0 0;
  font-family: var(--font-display, var(--font-mono));
  font-size: 19px;
  letter-spacing: 3px;
  color: var(--cyan-bright);
}

.vc-close {
  flex: 0 0 auto;
  width: 30px;
  height: 30px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: transparent;
  color: var(--text-muted);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  transition: 0.15s ease;
}
.vc-close:hover { border-color: var(--cyan); color: var(--cyan-bright); }

.vc-select {
  display: block;
  width: 100%;
  box-sizing: border-box;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 9px 11px;
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 12.5px;
  outline: none;
}
.vc-select:focus { border-color: var(--cyan); }

.vc-check {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  margin-top: 14px;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text);
  cursor: pointer;
}
.vc-check input { accent-color: var(--cyan); margin-top: 2px; cursor: pointer; }
.vc-hint-inline { color: var(--text-muted); }

.vc-intro {
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-muted);
  margin: 10px 0 0;
}

/* ---- the meter ---- */
.vc-meter-block {
  margin-top: 22px;
  padding: 18px 18px 20px;
  border: 1px solid var(--cyan-dim);
  border-radius: 8px;
  background: rgba(45, 226, 230, 0.035);
}

.vc-meter-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.vc-meter-title {
  font-family: var(--font-display, var(--font-mono));
  font-size: 11px;
  letter-spacing: 2px;
  color: var(--cyan-bright);
}

.vc-mode {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 4px 10px;
  border: 1px solid var(--border);
  border-radius: 20px;
  color: var(--text-muted);
}
.vc-mode.awake { border-color: var(--cyan); color: var(--cyan-bright); }
.vc-mode.suppressed { border-color: #ffcc66; color: #ffcc66; }

.vc-meter {
  position: relative;
  height: 16px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.03);
  overflow: hidden;
}

.vc-meter-fill {
  position: absolute;
  inset: 0 auto 0 0;
  background: linear-gradient(90deg, rgba(45, 226, 230, 0.35), var(--cyan));
  transition: width 0.12s linear;
}
.vc-meter.voiced .vc-meter-fill { background: linear-gradient(90deg, var(--cyan), var(--cyan-bright)); }

/* The loudest thing heard since the page opened. A reading taken
   between two words looks exactly like a dead microphone, and this is
   what tells them apart. */
.vc-meter-peak {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: #fff;
  opacity: 0.55;
  transition: left 0.2s ease;
}

.vc-meter-gate {
  position: absolute;
  top: -2px;
  bottom: -2px;
  left: 50%;
  width: 2px;
  background: #ffcc66;
}

.vc-meter-legend {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-top: 7px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-muted);
}
.vc-gate-label { color: #ffcc66; }

.vc-sensitivity { display: block; margin-top: 18px; }
.vc-sensitivity input { width: 100%; accent-color: var(--cyan); cursor: pointer; }

.vc-label {
  display: block;
  font-size: 9.5px;
  letter-spacing: 1.6px;
  color: var(--cyan-dim);
  margin: 0 0 6px;
}

.vc-hint {
  display: block;
  margin: 7px 0 0;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--text-muted);
}

/* ---- the chain ---- */
.vc-stages { list-style: none; margin: 22px 0 0; padding: 0; }

.vc-stage {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 6px;
  margin-bottom: 8px;
  background: rgba(255, 255, 255, 0.015);
}
.vc-stage.ok { border-color: var(--cyan-dim); background: rgba(45, 226, 230, 0.05); }

.vc-stage-number {
  flex: 0 0 auto;
  min-width: 1.6em;
  font-family: var(--font-mono);
  color: var(--cyan-dim);
}

.vc-stage-body { flex: 1; min-width: 0; }

.vc-stage-name {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}
.vc-stage.ok .vc-stage-name { color: var(--cyan-bright); }

.vc-stage-detail {
  margin: 3px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-muted);
}

.vc-stage-fix {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: #ffcc66;
}

.vc-stage-mark {
  flex: 0 0 auto;
  font-size: 14px;
  color: var(--cyan-bright);
  opacity: 0.8;
}
.vc-stage:not(.ok) .vc-stage-mark { color: var(--text-muted); opacity: 0.4; }

/* ---- the sentence in flight ---- */
.vc-utterance {
  margin-top: 18px;
  padding: 14px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--cyan);
  border-radius: 6px;
}

.vc-utterance-line {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text);
}

.vc-engine-error {
  margin-top: 16px;
  font-size: 12px;
  line-height: 1.55;
  color: var(--danger, #ff5470);
  word-break: break-word;
}

.vc-where {
  margin: 20px 0 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-muted);
}

@media (max-width: 620px) {
  .vc-backdrop { padding: 12px 10px; }
  .vc-panel { padding: 18px 16px 20px; }
  .vc-title { font-size: 16px; letter-spacing: 2px; }
  .vc-meter-legend { font-size: 9.5px; }
}

/* A short window - the panel scrolls inside the backdrop rather than
   running off the bottom of it. */
@media (max-height: 640px) {
  .vc-backdrop { align-items: flex-start; }
  .vc-panel { margin: 0 auto; }
}
</style>
