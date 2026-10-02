<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  ShutdownView.vue
  The power-down screen.

  The reference tried window.close() here, which browsers block for any
  window a script didn't open. This doesn't try: in app mode the backend
  closes the window itself during the ordered shutdown, so this screen is
  only ever glimpsed; in a tab there is nothing to close and saying so
  is more honest than a call that silently fails.
-->
<script setup>
import { onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { useChimes } from '@/composables/useChimes'
import { useIdentityStore } from '@/stores/identity'

const identity = useIdentityStore()

const chimes = useChimes()
const stopped = ref(false)

onMounted(async () => {
  // The chime plays to the end *before* the backend is told to go.
  //
  // It used to be the other way round: the UI asked for a shutdown and
  // the backend guessed a grace period - one second - before tearing
  // down and closing the window. The chime is four seconds long, so it
  // was cut off three seconds early every single time, and in app mode
  // the window vanished mid-sound.
  //
  // Whoever owns the audio should decide when it's finished, and that's
  // this view. The backend keeps a long backstop in case this never
  // reports back (a tab closed mid-chime), and the request below brings
  // it forward the moment the sound actually ends.
  await chimes.playUntilDone('shutdown')
  await api.shutdown().catch(() => {})
  stopped.value = true
})
</script>

<template>
  <div class="overlay">
    <div class="boot-ring shutting-down">
      <div class="ring r1" />
      <div class="ring r2" />
      <div class="ring r3" />
      <div class="boot-core" />
    </div>
    <p class="boot-text">{{ identity.brand }} IS POWERING DOWN</p>
    <p class="boot-subtext">
      {{ stopped ? 'Her backend has stopped. This window closes itself in a moment.'
                 : 'Closing things down in order…' }}
    </p>
    <p class="boot-subtext shutdown-hint">
      Start her again with <code>quietude</code>, or
      <code>systemctl --user start quietude</code>.
    </p>
  </div>
</template>

<style scoped>
.shutdown-hint {
  margin-top: 18px;
  opacity: 0.6;
  font-size: 11.5px;
}
.shutdown-hint code {
  color: var(--cyan);
  background: rgba(45, 226, 230, 0.1);
  padding: 1px 5px;
  border-radius: 3px;
}
</style>
