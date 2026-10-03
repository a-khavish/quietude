<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  BootRouteView.vue
  The landing route: asks the backend what state things are in and sends
  the user to the right screen.

  The reference did this in init() at the bottom of main.js, with the
  routing decision interleaved with DOM setup. Separating it means the
  decision is one function (entryRoute) that can be read on its own, and
  the "can't reach the backend" case gets a real screen instead of
  overwriting document.body with an error string.
-->
<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { entryRoute } from '@/router'
import { useIdentityStore } from '@/stores/identity'

const identity = useIdentityStore()

const router = useRouter()
const session = useSessionStore()
const error = ref(null)
const retrying = ref(false)

async function decide() {
  error.value = null
  retrying.value = true
  try {
    const status = await session.refreshStatus()
    router.replace(entryRoute(status))
  } catch (err) {
    error.value = err.message
  } finally {
    retrying.value = false
  }
}

onMounted(decide)
</script>

<template>
  <div class="overlay">
    <template v-if="error">
      <p class="boot-text offline">NO BACKEND</p>
      <p class="boot-subtext">{{ error }}</p>
      <p class="boot-subtext offline-hint">
        Start it with <code>quietude</code> in a terminal, or
        <code>systemctl --user start quietude</code>.
      </p>
      <button class="btn-primary retry" :disabled="retrying" @click="decide">
        {{ retrying ? 'Trying…' : 'Try again' }}
      </button>
    </template>

    <template v-else>
      <div class="boot-ring">
        <div class="ring r1" />
        <div class="ring r2" />
        <div class="ring r3" />
        <div class="boot-core" />
      </div>
      <p class="boot-text">WAKING {{ identity.brand }}</p>
    </template>
  </div>
</template>

<style scoped>
.offline { color: var(--danger); }
.offline-hint { margin-top: 14px; opacity: 0.7; font-size: 11.5px; }
.offline-hint code {
  color: var(--cyan);
  background: rgba(45, 226, 230, 0.1);
  padding: 1px 5px;
  border-radius: 3px;
}
.retry { width: auto; padding: 11px 22px; margin-top: 22px; }
</style>
