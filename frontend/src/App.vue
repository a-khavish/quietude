<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  App.vue
  The shell: background, scanlines, the current screen, and the two
  overlays that can appear over any of them.

  The hard speech gate lives here rather than in ConsoleView, because it
  has to be able to sit in front of the console *before* the console
  mounts - a half-loaded engine must not be reachable, not merely
  covered up. It shows once per session.
-->
<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useBoot } from '@/composables/useBoot'
import ParticleField from '@/components/ParticleField.vue'
import ScanLines from '@/components/ScanLines.vue'
import BootOverlay from '@/components/BootOverlay.vue'
import SpeechGate from '@/components/SpeechGate.vue'

const route = useRoute()
const session = useSessionStore()
const { boot } = useBoot()

const needsSpeechGate = computed(
  () => route.meta.gateSpeech === true && !session.speechGateResolved,
)

async function onSpeechReady() {
  // Status is refreshed *before* the flag flips, because flipping it is
  // what mounts the console - and the console reads speech readiness
  // during its entry sequence. Fetching afterwards would race it, and
  // the greeting would describe the state from app load rather than now.
  await session.refreshStatus().catch(() => {})
  session.speechGateTimedOut = false
  session.speechGateResolved = true
}

function onSpeechUnavailable(reason) {
  session.speechGateResolved = true
  session.speechGateTimedOut = true
  session.speech = { ...session.speech, ready: false, detail: reason }
}
</script>

<template>
  <ParticleField />
  <ScanLines />

  <RouterView v-slot="{ Component }">
    <!-- v-if, not just an overlay on top: a screen behind the speech
         gate must not mount yet. The console's entry sequence reads
         speech readiness to decide what to tell the user about voice,
         and running it while the models are still loading would greet
         them with "voice features are unavailable" and then leave that
         standing after the gate resolved successfully.

         Keyed on the route so a screen reached twice (two face
         registrations in a session, say) remounts and runs its setup
         again, rather than reusing stale local state. -->
    <component v-if="!needsSpeechGate" :is="Component" :key="route.fullPath" />
  </RouterView>

  <SpeechGate
    v-if="needsSpeechGate"
    @ready="onSpeechReady"
    @unavailable="onSpeechUnavailable"
  />

  <BootOverlay
    v-if="boot.visible"
    :label="boot.label"
    :subtext="boot.subtext"
    :shutting-down="boot.shuttingDown"
  />
</template>
