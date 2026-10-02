<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  PasswordLoginView.vue
  The backup password gate.

  Because this bypasses face recognition entirely, the backend tolerates
  three wrong attempts with a countdown and wipes all local data on the
  fourth. That policy lives in the backend; this view just renders what
  it says, including the warning in the initial prompt.
-->
<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import { useMessageLog, wait } from '@/composables/useMessageLog'
import { useBoot } from '@/composables/useBoot'
import { useChimes, STARTUP_SOUND_MS } from '@/composables/useChimes'
import HudPanel from '@/components/HudPanel.vue'
import ChatLog from '@/components/ChatLog.vue'

const router = useRouter()
const session = useSessionStore()
const chimes = useChimes()
const { run: runBoot } = useBoot()
const { messages, say, echo } = useMessageLog()

const input = ref('')
const inputEl = ref(null)
const busy = ref(false)

async function focusInput() {
  await nextTick()
  inputEl.value?.focus()
}

function renderStep(state) {
  say(state.prompt)
  input.value = ''
  focusInput()
}

onMounted(async () => {
  try {
    renderStep(await api.passwordLoginStart())
  } catch (err) {
    say(err.message, 'error')
  }
})

async function submit() {
  const value = input.value.trim()
  if (!value || busy.value) return
  busy.value = true

  echo(value, { masked: true })
  input.value = ''

  try {
    const data = await api.passwordLoginAnswer(value)

    if (!data.ok) {
      say(data.error || "That didn't match - please try again.", 'error')
      if (data.reset_triggered) {
        // The wipe already happened server-side. Let the message land,
        // then go through the reset animation so it's unmistakable.
        await wait(1800)
        router.push({ name: 'reset' })
        return
      }
      if (data.state) renderStep(data.state)
      return
    }

    say('Correct.', 'success')

    if (data.user) {
      session.authenticate({ ...data.user, face_registered: session.faceRegistered })
      await wait(500)
      chimes.play('startup')
      await runBoot(() => router.push({ name: 'console' }), {
        label: 'WELCOME BACK',
        duration: STARTUP_SOUND_MS,
      })
      return
    }

    await wait(300)
    renderStep(data.state)
  } catch (err) {
    // A 400 with the backend's own message (a wrong password, say)
    // arrives here via ApiError; its text is already the right thing to
    // show, so there's nothing to translate.
    say(err.message, 'error')
    if (err.payload?.state) renderStep(err.payload.state)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="screen">
    <HudPanel variant="setup-panel">
      <div class="panel-header">
        <span class="eyebrow">IDENTITY VERIFICATION</span>
        <h1 class="wordmark small">PASSWORD LOGIN</h1>
      </div>

      <ChatLog :messages="messages" class="setup-log" />

      <form class="input-row" @submit.prevent="submit">
        <span class="prompt-caret">&gt;</span>
        <input
          ref="inputEl"
          v-model="input"
          type="password"
          autocomplete="off"
          placeholder="Type your answer..."
          :disabled="busy"
        />
        <button type="submit" class="btn-send" :disabled="busy">SEND</button>
      </form>

      <button type="button" class="back-link" @click="router.push({ name: 'login' })">
        &larr; Back to login options
      </button>
    </HudPanel>
  </div>
</template>
