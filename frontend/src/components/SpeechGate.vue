<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  SpeechGate.vue
  The hard readiness gate. Nobody reaches the console until both speech
  models confirm they're loaded.

  Why this one is hard where the TTS gate is soft: a half-loaded speech
  engine can't reliably hear a wake word, and an assistant that looks
  like it's listening but isn't is worse than one that says it isn't
  ready. Quietude being unable to *speak* yet, by contrast, costs nothing -
  you can read her replies.

  It is not, however, infinitely hard. Blocking forever on a broken
  install would make text chat unreachable too, which is a far worse
  outcome than no voice. So after SPEECH_GATE_TIMEOUT_MS, or on a
  definite error from the engine, it fails open into "voice features
  unavailable, text chat still works" and lets the user through.

  Progress text names whichever model is still loading, rather than
  showing one undifferentiated spinner for what can be a minute on
  slower hardware.
-->
<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/api/client'
import { SPEECH_GATE_TIMEOUT_MS } from '@/stores/session'

const emit = defineEmits(['ready', 'unavailable'])

const POLL_INTERVAL_MS = 400

const detail = ref('Starting speech engine...')
const elapsed = ref(0)
const failed = ref(false)
const failureReason = ref('')

let timer = null
let startedAt = 0
let settled = false

function settle(event, payload) {
  if (settled) return
  settled = true
  clearTimeout(timer)
  emit(event, payload)
}

function failOpen(reason) {
  failed.value = true
  failureReason.value = reason
  // Hold the message on screen briefly so it is actually read, rather
  // than flashing past on the way to the console.
  setTimeout(() => settle('unavailable', reason), 2600)
}

async function poll() {
  let data
  try {
    data = await api.speechStatus()
  } catch {
    // The backend not answering yet is normal during startup; only the
    // overall timeout decides this has gone wrong.
    data = null
  }

  elapsed.value = Date.now() - startedAt

  if (data) {
    if (data.ready) {
      detail.value = 'Speech engine ready.'
      settle('ready')
      return
    }

    // A definite error - a missing model, a package that isn't installed
    // - will not resolve by waiting, so don't make the user wait out the
    // full timeout for it.
    if (data.error) {
      failOpen(data.error)
      return
    }

    if (!data.vosk_ready) detail.value = 'Loading wake-word model...'
    else if (!data.whisper_ready) detail.value = 'Loading speech-to-text model...'
    else detail.value = 'Starting speech engine...'
  }

  if (elapsed.value > SPEECH_GATE_TIMEOUT_MS) {
    failOpen("The speech engine didn't finish starting up.")
    return
  }

  timer = setTimeout(poll, POLL_INTERVAL_MS)
}

onMounted(() => {
  startedAt = Date.now()
  poll()
})

onUnmounted(() => {
  settled = true
  clearTimeout(timer)
})
</script>

<template>
  <div class="overlay">
    <div class="boot-ring">
      <div class="ring r1" />
      <div class="ring r2" />
      <div class="ring r3" />
      <div class="boot-core" />
    </div>

    <template v-if="failed">
      <p class="boot-text">VOICE UNAVAILABLE</p>
      <p class="boot-subtext gate-error">{{ failureReason }}</p>
      <p class="boot-subtext">Text chat still works - taking you through.</p>
    </template>
    <template v-else>
      <p class="boot-text">PREPARING SPEECH ENGINE</p>
      <p class="boot-subtext">{{ detail }}</p>
      <p v-if="elapsed > 15000" class="boot-subtext gate-hint">
        First run loads two models from disk - this is slowest the first time.
      </p>
    </template>
  </div>
</template>

<style scoped>
.gate-error { color: var(--warning); }
.gate-hint { opacity: 0.65; font-size: 11px; margin-top: 4px; }
</style>
