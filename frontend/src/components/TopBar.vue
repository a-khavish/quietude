<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  TopBar.vue
  Brand, clock, mode indicator, mic toggle, shut down.

  The mode indicator is the one piece of UI here carrying a real
  warning: cyan STATIC means everything is local, purple CONVERSATION
  means what you type is going to Google. It also shows the turn counter
  against the history cap while conversation mode is on.
-->
<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { useIdentityStore } from '@/stores/identity'

const identity = useIdentityStore()
const props = defineProps({
  conversationMode: { type: Boolean, default: false },
  conversationTurns: { type: Object, default: null },
  voiceState: { type: String, default: 'off' },  // 'off' | 'asleep' | 'awake'
  voiceAvailable: { type: Boolean, default: false },
  voiceBusy: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle-voice', 'shutdown', 'voice-check'])

const now = ref(new Date())
const timer = setInterval(() => { now.value = new Date() }, 1000)
onUnmounted(() => clearInterval(timer))

const clock = computed(() => now.value.toLocaleTimeString())

const modeLabel = computed(() => {
  if (!props.conversationMode) return 'STATIC'
  const t = props.conversationTurns
  return t ? `CONVERSATION (${t.used}/${t.cap})` : 'CONVERSATION'
})

const modeTitle = computed(() =>
  props.conversationMode
    ? 'Conversation mode is ON - talking to Gemini, not fully local'
    : 'Static commands mode - fully local',
)

const voiceTitle = computed(() => {
  if (!props.voiceAvailable) return 'Voice chat is unavailable - the local speech models are not loaded'
  if (props.voiceBusy) return 'Starting voice chat...'
  if (props.voiceState === 'off') return 'Voice chat is OFF - click or say "turn on voice chat" to start'
  if (props.voiceState === 'asleep') {
    // The real phrase, not a written-out example: the wake word is
    // built from the name the user chose, and a tooltip naming a
    // different one would be actively misleading.
    const phrase = identity.wakePhrase || 'the wake phrase'
    return `Voice chat is ON - say "${phrase}" to give me a command, or click to turn off`
  }
  return 'Listening for your command...'
})
</script>

<template>
  <div class="topbar">
    <div class="brand">
      <div class="brand-orb"><span class="core" /></div>
      <span class="brand-name">{{ identity.brand }}</span>
      <span class="status-pill"><span class="dot" /> ONLINE</span>
    </div>

    <div class="topbar-right">
      <span class="clock">{{ clock }}</span>

      <div class="mode-indicator" :class="{ conversation: conversationMode }" :title="modeTitle">
        <span class="mode-dot" />
        <span class="mode-label">{{ modeLabel }}</span>
      </div>

      <button
        type="button"
        class="voice-btn"
        :class="{
          listening: voiceState === 'asleep',
          awake: voiceState === 'awake',
          unsupported: !voiceAvailable,
        }"
        :aria-pressed="voiceState !== 'off'"
        :title="voiceTitle"
        :disabled="voiceBusy"
        @click="emit('toggle-voice')"
      >
        <span class="voice-pulse" />
        <svg viewBox="0 0 24 24" fill="none" class="voice-icon">
          <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Z"
                stroke="currentColor" stroke-width="1.6"
                stroke-linecap="round" stroke-linejoin="round" />
          <path d="M19 11a7 7 0 0 1-14 0" stroke="currentColor"
                stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
          <path d="M12 18v3" stroke="currentColor" stroke-width="1.6"
                stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>

      <!--
        The way into the voice check that does not require being heard.

        It used to be a typed command, which cannot be typed: the
        message box is deliberately locked while voice chat is on. So
        the screen that exists for "she cannot hear me" was reachable
        only by saying something to her, and the people who need it are
        exactly the people she is not hearing. It is a button, and it
        is here whenever voice chat is available.
      -->
      <button
        v-if="voiceAvailable"
        type="button"
        class="voice-check-btn"
        title="Voice check - what is she hearing?"
        aria-label="Voice check"
        @click="emit('voice-check')"
      >
        <svg viewBox="0 0 24 24" fill="none">
          <path d="M4 14v-4M8 17V7M12 20V4M16 17V7M20 14v-4"
                stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
        </svg>
      </button>

      <button type="button" class="btn-ghost danger" @click="emit('shutdown')">
        SHUT DOWN
      </button>
    </div>
  </div>
</template>
