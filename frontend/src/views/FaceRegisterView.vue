<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  FaceRegisterView.vue
  Captures face samples and trains the local recognizer.

  The capture loop is sequential and self-scheduling - call, await,
  schedule the next - rather than a setInterval. That detail is carried
  over deliberately: setInterval fires on a fixed clock whether or not
  the previous request finished, so one slow request puts several in
  flight at once, each able to independently "succeed". In the reference
  that was the cause of samples overshooting their target count. Only
  ever one request is in flight here.

  Every attempt also starts from zero samples. There's no guarantee the
  person at the camera now is the person who started an earlier,
  abandoned attempt, so resuming one would be a real security hole
  rather than a convenience.
-->
<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import { useChatStore } from '@/stores/chat'
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
const chat = useChatStore()
const camera = useCamera()
const chimes = useChimes()
const { run: runBoot } = useBoot()

const REQUIRED_SAMPLES = 18
const CAPTURE_INTERVAL_MS = 350

const frame = ref(null)
const samples = ref(0)
const required = ref(REQUIRED_SAMPLES)
const status = ref('')
const statusKind = ref('')      // '' | 'error' | 'success'
const showRetry = ref(false)
const success = ref(false)

// 'first' means this is the tail of first-run setup, so finishing boots
// into the console. Otherwise it was launched from the chat, which means
// a Back button and a confirmation bubble on return.
const isFirstRun = computed(() => route.query.first === '1')

let active = false
let cancelled = false

const progressPct = computed(() =>
  Math.min(100, Math.round((samples.value / required.value) * 100)),
)

function fail(message) {
  active = false
  camera.stop()
  status.value = message
  statusKind.value = 'error'
  showRetry.value = true
}

async function begin() {
  showRetry.value = false
  status.value = ''
  statusKind.value = ''
  success.value = false
  samples.value = 0

  // Wipe anything a previous attempt left behind before capturing.
  await api.faceResetSamples().catch(() => {})

  const started = await camera.start(frame.value?.video)
  if (!started) {
    fail(camera.error.value || 'Camera access is required to register your face.')
    return
  }

  active = true
  captureStep()
}

async function captureStep() {
  if (!active || cancelled) return

  const image = camera.captureFrame(frame.value?.video)
  if (!image) {
    // The video element hasn't produced a frame yet.
    await wait(300)
    return captureStep()
  }

  let data
  try {
    data = await api.faceCapture(image)
  } catch (err) {
    if (!active) return
    if (err.offline) {
      await wait(500)
      return captureStep()
    }
    fail(err.message)
    return
  }

  if (!active || cancelled) return

  if (!data.ok) {
    fail(data.error || 'Face detection failed on the backend.')
    return
  }

  if (!data.detected) {
    status.value = data.message || 'Center your face in the frame.'
    await wait(CAPTURE_INTERVAL_MS)
    return captureStep()
  }

  required.value = data.required || REQUIRED_SAMPLES
  samples.value = data.count
  status.value = 'Great, keep still...'

  if (samples.value >= required.value) {
    active = false
    camera.stop()
    await train()
    return
  }

  await wait(CAPTURE_INTERVAL_MS)
  return captureStep()
}

async function train() {
  status.value = 'Training face model...'
  try {
    const data = await api.faceTrain()
    if (!data.ok) {
      fail("Face training failed - let's try capturing again.")
      return
    }
  } catch (err) {
    fail(err.message)
    return
  }

  session.markFaceRegistered()

  // Hold the green flash long enough to read before moving on.
  success.value = true
  status.value = 'Face registered!'
  statusKind.value = 'success'
  await wait(1800)

  await finish()
}

async function finish() {
  if (isFirstRun.value) {
    chimes.play('startup')
    await runBoot(() => router.push({ name: 'console', query: { first: '1' } }), {
      label: `INITIALIZING ${identity.brand}`,
      duration: STARTUP_SOUND_MS,
    })
  } else {
    chat.addQuietude('Face saved! Registration completed successfully.', 'success')
    router.push({ name: 'console' })
  }
}

function cancel() {
  cancelled = true
  active = false
  camera.stop()
  // Clean up partial biometric data straight away rather than leaving it
  // until the next attempt wipes it.
  api.faceResetSamples().catch(() => {})
  chat.addQuietude('Face Registration has been cancelled.', 'warning')
  router.push({ name: 'console' })
}

onMounted(begin)
onUnmounted(() => { active = false; camera.stop() })
</script>

<template>
  <div class="screen">
    <HudPanel variant="face-panel">
      <div class="panel-header">
        <span class="eyebrow">SECURE LOGIN SETUP</span>
        <h1 class="wordmark small">FACE REGISTRATION</h1>
      </div>

      <p class="face-instructions">
        Let's register your face so only you can unlock {{ identity.displayName }}. Slowly turn your head
        left, right, and center while I learn your face. Stay in good lighting.
      </p>

      <CameraFrame ref="frame" :success="success" />

      <div class="face-progress">
        <div class="face-progress-bar">
          <div class="face-progress-fill" :style="{ width: progressPct + '%' }" />
        </div>
        <p class="face-progress-label">
          {{ samples }} / {{ required }} samples captured
        </p>
      </div>

      <p class="face-status" :class="statusKind">{{ status }}</p>

      <button v-if="showRetry" class="btn-primary" @click="begin">Try Again</button>
      <button v-if="!isFirstRun" type="button" class="back-link" @click="cancel">
        &larr; Back to main chat
      </button>
    </HudPanel>
  </div>
</template>
