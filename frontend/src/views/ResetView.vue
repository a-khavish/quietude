<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  ResetView.vue
  The factory-reset animation.

  The progress bar is timed to the length of reset.mp3 rather than to the
  actual wipe, which finishes almost instantly - so the sound and the
  visual end together instead of the bar snapping to full over a chime
  that's still playing.

  Afterwards it routes to the very beginning rather than reloading the
  page. The reference had to reload, and had to set a flag first so its
  pagehide handler wouldn't mistake the reload for the window closing and
  shut the server down. With no heartbeat watchdog there's nothing to
  suppress, and a router push is both faster and less violent.
-->
<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import { useChatStore } from '@/stores/chat'
import { useChimes, RESET_SOUND_MS } from '@/composables/useChimes'
import { useIdentityStore } from '@/stores/identity'

const identity = useIdentityStore()

const router = useRouter()
const session = useSessionStore()
const chat = useChatStore()
const chimes = useChimes()

const progress = ref(0)
const failed = ref(null)

onMounted(async () => {
  chimes.play('reset')

  // Start the wipe and the animation together; await the request only
  // after the bar has filled, so a fast wipe doesn't cut the chime off.
  const wipe = api.resetExecute().catch((err) => { failed.value = err.message })

  const start = performance.now()
  await new Promise((resolve) => {
    const tick = (now) => {
      progress.value = Math.min(100, ((now - start) / RESET_SOUND_MS) * 100)
      if (progress.value < 100) requestAnimationFrame(tick)
      else resolve()
    }
    requestAnimationFrame(tick)
  })

  await wipe

  session.reset()
  chat.clear()
  await session.refreshStatus().catch(() => {})
  router.push({ name: 'agreement' })
})
</script>

<template>
  <div class="overlay">
    <p class="boot-text reset-text">RESETTING {{ identity.brand }}</p>
    <div class="reset-progress-bar">
      <div class="reset-progress-fill" :style="{ width: progress + '%' }" />
    </div>
    <p class="boot-subtext">
      {{ failed || 'Erasing all saved data...' }}
    </p>
  </div>
</template>
