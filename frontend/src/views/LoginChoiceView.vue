<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  LoginChoiceView.vue
  Face or password, for a returning user.

  The choice is always offered, even when no face is registered - the
  face option is visibly unavailable and explains itself rather than
  forcing registration before granting access. Password login always
  works, which is the entire point of having a backup.
-->
<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import HudPanel from '@/components/HudPanel.vue'

const router = useRouter()
const session = useSessionStore()
const showFaceWarning = ref(false)

// The profile is fetched to greet the user by name, and kept local
// rather than written into the session store. Putting it there would
// make merely arriving at this screen look like a completed login to
// the router guard, which would let the password gate be skipped.
const profile = ref(null)

const name = computed(() => profile.value?.name || 'QUIETUDE')

onMounted(async () => {
  const data = await api.user().catch(() => null)
  if (data?.ok) {
    profile.value = data.user
    // faceRegistered is not a secret and decides whether the face
    // button is offered, so it's fine to keep in the store.
    session.faceRegistered = !!data.user.face_registered
  }
})

function chooseFace() {
  if (!session.faceRegistered) {
    showFaceWarning.value = true
    return
  }
  router.push({ name: 'face-unlock' })
}
</script>

<template>
  <div class="screen">
    <HudPanel variant="login-choice-panel">
      <div class="panel-header">
        <span class="eyebrow">WELCOME BACK</span>
        <h1 class="wordmark small">{{ name }}</h1>
      </div>

      <p class="face-instructions">How would you like to log in?</p>

      <div class="login-choice-buttons">
        <button
          type="button"
          class="btn-primary login-choice-btn"
          :class="{ 'face-unavailable': !session.faceRegistered }"
          @click="chooseFace"
        >
          Login via Face Recognition
        </button>
        <button
          type="button"
          class="btn-primary login-choice-btn ghost-variant"
          @click="router.push({ name: 'password-login' })"
        >
          Login via Password
        </button>
      </div>

      <p v-if="showFaceWarning" class="login-face-warning">
        Face recognition hasn't been registered yet - please use Login via Password instead.
      </p>
    </HudPanel>
  </div>
</template>
