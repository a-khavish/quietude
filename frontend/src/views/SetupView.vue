<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  SetupView.vue
  First-run account setup, as a conversation.

  The wizard itself is a backend state machine (core/setup_wizard.py),
  unchanged. This view renders the prompt, submits the answer, and shows
  the pass/fail result - and because progress is saved after every
  successful step, closing Quietude mid-setup and coming back resumes
  exactly where you left off.
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
import { useIdentityStore } from '@/stores/identity'

const identity = useIdentityStore()

const router = useRouter()
const session = useSessionStore()
const chimes = useChimes()
const { run: runBoot } = useBoot()

// Its own log, separate from the main console's - a setup conversation
// (and the password prompt in particular) has no business showing up in
// the chat history afterwards.
const { messages, say, echo } = useMessageLog()

const input = ref('')
const inputEl = ref(null)
const inputType = ref('text')
const busy = ref(false)
const pausing = ref(false)

async function focusInput() {
  await nextTick()
  inputEl.value?.focus()
}

function renderStep(state) {
  if (state.done) return
  inputType.value = state.expect === 'password' ? 'password' : 'text'
  say(state.prompt + (state.requirements ? `\n${state.requirements}` : ''))
  input.value = ''
  focusInput()
}

onMounted(async () => {
  try {
    const state = await api.setupState()
    if (state.step === 'full_name' && !state.done) {
      say("Let's get some information about you before we start...")
    } else {
      say("Welcome back - let's pick up where we left off.")
    }
    renderStep(state)
  } catch (err) {
    say(err.message, 'error')
  }
})

async function submit() {
  const value = input.value.trim()
  if (!value || busy.value) return
  busy.value = true

  echo(value, { masked: inputType.value === 'password' })
  input.value = ''

  try {
    const data = await api.setupAnswer(value)

    if (!data.ok) {
      say(data.error || "Something's not right - let's try that again.", 'error')
      if (data.state) renderStep(data.state)
      return
    }

    say('Saved.', 'success')

    if (data.user) {
      await finish(data)
      return
    }

    await wait(300)
    renderStep(data.state)
  } catch (err) {
    say(err.message, 'error')
  } finally {
    busy.value = false
  }
}

async function finish(data) {
  // Finishing setup is itself the login for a first run.
  session.authenticate({ ...data.user, face_registered: false })

  if (data.register_face) {
    say(`Nice to meet you, ${data.user.name}! One last step - let's register your face.`)
    await wait(1000)
    await runBoot(() => router.push({ name: 'face-register', query: { first: '1' } }), {
      label: 'PREPARING FACE RECOGNITION',
      subtext: 'Find a well-lit space and look directly at the camera when ready.',
      duration: 3200,
    })
  } else {
    say(`Nice to meet you, ${data.user.name}! You're all set.`)
    await wait(1000)
    chimes.play('startup')
    await runBoot(() => router.push({ name: 'console', query: { first: '1' } }), {
      label: `INITIALIZING ${identity.brand}`,
      duration: STARTUP_SOUND_MS,
    })
  }
}

/**
 * Pause and exit. Progress is already on disk after every successful
 * step, so there is nothing to save here - this just shuts Quietude down the
 * same way the SHUT DOWN button does, with a visible countdown so it
 * doesn't look like a crash.
 */
async function pauseAndExit() {
  if (pausing.value) return
  pausing.value = true

  let secondsLeft = 5
  const line = (s) => `Pausing here - your progress is saved. Shutting down in ${s}...`
  const bubble = say(line(secondsLeft), 'warning')

  const countdown = setInterval(() => {
    secondsLeft -= 1
    bubble.text = secondsLeft > 0
      ? line(secondsLeft)
      : 'Pausing here - your progress is saved. Shutting down now...'
    if (secondsLeft <= 0) clearInterval(countdown)
  }, 1000)

  await wait(5000)
  // Same as the console's button: the shutdown screen plays the chime
  // through and asks the backend afterwards.
  router.push({ name: 'shutdown' })
}
</script>

<template>
  <div class="screen">
    <HudPanel variant="setup-panel">
      <div class="panel-header">
        <span class="eyebrow">ACCOUNT SETUP</span>
        <h1 class="wordmark small">{{ identity.brand }}</h1>
        <button
          type="button"
          class="btn-ghost setup-pause-btn"
          :disabled="pausing"
          @click="pauseAndExit"
        >
          PAUSE / EXIT
        </button>
      </div>

      <ChatLog :messages="messages" class="setup-log" />

      <form class="input-row" @submit.prevent="submit">
        <span class="prompt-caret">&gt;</span>
        <input
          ref="inputEl"
          v-model="input"
          :type="inputType"
          autocomplete="off"
          placeholder="Type your answer..."
          :disabled="busy || pausing"
        />
        <button type="submit" class="btn-send" :disabled="busy || pausing">SEND</button>
      </form>
    </HudPanel>
  </div>
</template>
