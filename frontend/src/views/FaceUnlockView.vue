<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  FaceUnlockView.vue
  Live face unlock. Used both for login and as the gate in front of a
  factory reset - same verification, two destinations.

  Three consecutive matching frames are required before it unlocks. LBPH
  is not bank-vault-grade biometrics and a single stray low-confidence
  frame is possible, so one match is not enough on its own; the backup
  password exists for exactly the cases where this isn't reliable.

  Same sequential loop as registration, for the same reason - the
  reference traced duplicate welcome messages on login to overlapping
  setInterval requests each independently succeeding.
-->
<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import { useCamera } from '@/composables/useCamera'
import { useBoot } from '@/composables/useBoot'
import { useChimes, STARTUP_SOUND_MS } from '@/composables/useChimes'
import { wait } from '@/composables/useMessageLog'
import HudPanel from '@/components/HudPanel.vue'
import CameraFrame from '@/components/CameraFrame.vue'
import { useIdentityStore } from '@/stores/identity'

const identity = useIdentityStore()

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const camera = useCamera()
const chimes = useChimes()
const { run: runBoot } = useBoot()

const REQUIRED_CONSECUTIVE_MATCHES = 3
const MAX_ATTEMPTS = 60
const VERIFY_INTERVAL_MS = 900

const frame = ref(null)
const status = ref('Scanning...')
const statusKind = ref('')
const showRetry = ref(false)
const success = ref(false)

// 'reset' means this is the confirmation gate in front of a factory
// reset rather than a login.
const forReset = computed(() => route.query.purpose === 'reset')

let active = false
let matches = 0
let attempts = 0

function fail(message) {
  active = false
  camera.stop()
  status.value = message
  statusKind.value = 'error'
  showRetry.value = true
}

async function begin() {
  showRetry.value = false
  success.value = false
  status.value = 'Scanning...'
  statusKind.value = ''
  matches = 0
  attempts = 0

  const started = await camera.start(frame.value?.video)
  if (!started) {
    fail(camera.error.value || `Camera access is required to unlock ${identity.displayName}.`)
    return
  }

  active = true
  verifyStep()
}

async function verifyStep() {
  if (!active) return

  const image = camera.captureFrame(frame.value?.video)
  if (!image) {
    await wait(400)
    return verifyStep()
  }

  let data
  try {
    data = await api.faceVerify(image)
  } catch (err) {
    if (!active) return
    if (err.offline) {
      await wait(900)
      return verifyStep()
    }
    fail(err.message)
    return
  }

  if (!active) return
  attempts += 1

  if (!data.ok) {
    fail(data.error || 'Face verification failed on the backend.')
    return
  }

  if (!data.detected) {
    status.value = 'Center your face in the frame...'
    matches = 0
  } else if (data.matched) {
    matches += 1
    status.value = `Face recognized (${matches}/${REQUIRED_CONSECUTIVE_MATCHES})...`
    if (matches >= REQUIRED_CONSECUTIVE_MATCHES) {
      active = false
      success.value = true
      status.value = 'Face recognized!'
      statusKind.value = 'success'
      await wait(1800)
      camera.stop()
      await unlocked()
      return
    }
  } else {
    matches = 0
    status.value = 'Face not recognized. Keep looking at the camera...'
  }

  if (attempts > MAX_ATTEMPTS) {
    fail('Having trouble recognizing you. You can retry when ready.')
    return
  }

  await wait(VERIFY_INTERVAL_MS)
  return verifyStep()
}

async function unlocked() {
  if (forReset.value) {
    router.push({ name: 'reset' })
    return
  }
  await session.refreshUser().catch(() => {})
  session.authenticate(session.user)
  chimes.play('startup')
  await runBoot(() => router.push({ name: 'console' }), {
    label: 'WELCOME BACK',
    duration: STARTUP_SOUND_MS,
  })
}

function back() {
  active = false
  camera.stop()
  router.push({ name: forReset.value ? 'console' : 'login' })
}

onMounted(begin)
onUnmounted(() => { active = false; camera.stop() })
</script>

<template>
  <div class="screen">
    <HudPanel variant="face-panel">
      <div class="panel-header">
        <span class="eyebrow">IDENTITY VERIFICATION</span>
        <h1 class="wordmark small">
          {{ forReset ? 'CONFIRM IT\'S YOU' : 'FACE UNLOCK' }}
        </h1>
      </div>

      <p class="face-instructions">
        {{ forReset
          ? "Before erasing everything, look directly at the camera so I know it's you."
          : 'Look directly at the camera to sign in.' }}
      </p>

      <CameraFrame ref="frame" :success="success" />

      <p class="face-status" :class="statusKind">{{ status }}</p>

      <button v-if="showRetry" class="btn-primary" @click="begin">Retry</button>
      <button type="button" class="back-link" @click="back">
        &larr; {{ forReset ? 'Cancel' : 'Back to login options' }}
      </button>
    </HudPanel>
  </div>
</template>
