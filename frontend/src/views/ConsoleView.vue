<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  ConsoleView.vue
  The main interface: chat log, input, voice, and the actions Quietude's
  replies can trigger.

  This is where the reference's ~700 lines of orchestration lived. The
  shape is the same - a reply comes back carrying optional action /
  voice / restart / shutdown fields, and this decides what happens next -
  but the dispatch is a lookup rather than an if/else chain, the state is
  reactive rather than a dozen module-level flags, and the two actions
  that used to open separate browser windows are router pushes.
-->
<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import { useChatStore } from '@/stores/chat'
import { useIdentityStore } from '@/stores/identity'
import { useSharedTts } from '@/composables/useTts'
import { useVoiceChat } from '@/composables/useVoiceChat'
import { useBoot } from '@/composables/useBoot'
import { wait } from '@/composables/useMessageLog'
import HudPanel from '@/components/HudPanel.vue'
import AutoTextarea from '@/components/AutoTextarea.vue'
import ChatLog from '@/components/ChatLog.vue'
import TopBar from '@/components/TopBar.vue'
import SidePanel from '@/components/SidePanel.vue'
import TtsPlaybackBar from '@/components/TtsPlaybackBar.vue'

const router = useRouter()
const session = useSessionStore()
const chat = useChatStore()
const identity = useIdentityStore()
const tts = useSharedTts()
const { run: runBoot } = useBoot()

const FACE_REGISTER_COUNTDOWN_S = 10

// How tall the message box is allowed to get before it scrolls inside
// instead. Six lines is roughly a paragraph: enough to read back a long
// command before sending it, and short enough that the transcript above
// it stays the larger half of the panel.
const CHAT_INPUT_MAX_ROWS = 6

const input = ref('')
const inputEl = ref(null)
const fileEl = ref(null)
const sending = ref(false)
const locked = ref(false)        // during the face-registration countdown
const expectMode = ref('text')   // 'text' | 'password' | 'confirm'
const speakingMessageId = ref(null)

const voice = useVoiceChat({
  sendMessage: (text) => send(text, { spoken: true }),
  addQuietude: (text, status) => chat.addQuietude(text, status),
})

const inputType = computed(() => (expectMode.value === 'password' ? 'password' : 'text'))
const voiceOn = computed(() => voice.state.value !== 'off')
const inputDisabled = computed(() => sending.value || locked.value || voiceOn.value)

const placeholder = computed(() => {
  if (voice.state.value === 'asleep') return 'Say "Hello Quietude" to give a voice command...'
  if (voice.state.value === 'awake') return 'Listening...'
  return 'Type a command...'
})

// While voice chat is on the box shows what Vosk is hearing: red while
// asleep (sound is reaching her but nothing is acted on), green once
// awake (this is about to become a command).
const liveText = computed(() => (voiceOn.value ? voice.partialText.value : ''))
const displayValue = computed(() => (voiceOn.value ? liveText.value : input.value))

async function focusInput() {
  await nextTick()
  if (!inputDisabled.value) inputEl.value?.focus()
}

/* ---------------- reply handling ---------------- */

/**
 * A reply may use a blank line to mean "this should read as two
 * messages" - a disclaimer, then the question that follows it. Render
 * those as separate bubbles with a beat between, as the reference did.
 */
async function addReply(text, status) {
  const parts = text.split('\n\n').map((p) => p.trim()).filter(Boolean)
  const added = []
  for (let i = 0; i < parts.length; i++) {
    added.push(chat.addQuietude(parts[i], status))
    if (i < parts.length - 1) await wait(500)
  }
  return added
}

const ACTIONS = {
  clear_terminal() {
    chat.clear()
  },
  show_commands() {
    router.push({ name: 'commands' })
  },
  show_tts_settings() {
    router.push({ name: 'tts-settings' })
  },
  show_voice_library() {
    router.push({ name: 'voices' })
  },
  show_identity() {
    router.push({ name: 'identity' })
  },
  show_speech_models() {
    router.push({ name: 'models' })
  },
  conversation_mode_on(data) {
    session.setConversationMode(true, data.conversation_turns)
  },
  conversation_mode_off() {
    session.setConversationMode(false)
  },
  async register_face() {
    await voice.stop()
    await startFaceRegistration()
  },
  async reset_confirm() {
    await voice.stop()
    await wait(700)
    router.push({ name: 'face-unlock', query: { purpose: 'reset' } })
  },
}

async function send(message, { spoken = false } = {}) {
  const text = message?.trim()
  if (!text || sending.value) return
  sending.value = true

  chat.addUser(text, { masked: expectMode.value === 'password' })

  try {
    const data = await api.chat(text)

    // Captured before anything below changes it - this is what lets the
    // final "Voice chat is off." reply still be spoken aloud, even
    // though handling this very reply is what turns voice chat off.
    const wasVoiceActive = voiceOn.value

    if (data.action === 'clear_terminal') {
      ACTIONS.clear_terminal()
      expectMode.value = data.expect || 'text'
      return
    }

    // Synthesis starts now, in parallel with the bubbles appearing,
    // rather than after them. A reply containing a blank line renders as
    // two bubbles with a deliberate 500ms pause between them, and
    // waiting for that to finish before even asking for audio put half a
    // second of dead air in front of every multi-part answer - on top of
    // the round trip. The text is known the moment the reply arrives, so
    // there is nothing to wait for.
    const speaking = wasVoiceActive && data.reply ? voice.sayAloud(data.reply) : null

    await addReply(data.reply, data.status)
    if (data.table) chat.addTable(data.table)

    // Still awaited, so the voice state machine only moves on once she
    // has genuinely finished speaking.
    if (speaking) await speaking

    expectMode.value = data.expect || 'text'

    // An ordinary exchange while already in conversation mode: the mode
    // hasn't changed, only the turn count.
    if (!data.action && data.conversation_turns) {
      session.setConversationMode(true, data.conversation_turns)
    }

    if (data.voice === 'start') await startVoice()
    else if (data.voice === 'stop') await voice.stop()

    if (data.shutdown) {
      await voice.stop()
      await wait(600)
      router.push({ name: 'shutdown' })
      return
    }

    if (data.restart) {
      await wait(800)
      await softRestart()
      return
    }

    await ACTIONS[data.action]?.(data)
  } catch (err) {
    chat.addQuietude(err.message, 'error')
  } finally {
    sending.value = false
    if (!spoken) focusInput()
  }
}

/**
 * A command clicked in the sidebar.
 *
 * Put through send() rather than into the input box: a suggestion you
 * have to then press Enter on is a suggestion that only saved you some
 * typing, and the whole point of making these buttons was that they do
 * the thing. The log shows it as a message you sent, because that is
 * exactly what it is.
 */
function runSuggestion(command) {
  if (inputDisabled.value) return
  input.value = ''
  send(command)
}

function submit() {
  const text = input.value
  input.value = ''
  send(text)
}

/**
 * Re-runs the boot animation and refreshes the displayed profile after
 * an account change, without restarting the backend process.
 */
async function softRestart() {
  await runBoot(async () => {
    const user = await session.refreshUser().catch(() => null)
    if (user) chat.addQuietude(`All set, ${user.name}. What else can I help with?`)
  }, { label: 'APPLYING CHANGES' })
}

/* ---------------- face registration from chat ---------------- */

/**
 * Advises good lighting with a live countdown, with the chat locked so
 * nothing can be sent during it, then hands over to the registration
 * screen.
 */
async function startFaceRegistration() {
  locked.value = true

  let secondsLeft = FACE_REGISTER_COUNTDOWN_S
  const advice = (s) =>
    "Please make sure you're in a well-lit room with your face clearly visible to the " +
    `camera. Starting face registration in ${s}...`
  const bubble = chat.addQuietude(advice(secondsLeft), 'warning')

  const countdown = setInterval(() => {
    secondsLeft -= 1
    chat.updateText(
      bubble,
      secondsLeft > 0 ? advice(secondsLeft) : 'Starting face registration now...',
    )
    if (secondsLeft <= 0) clearInterval(countdown)
  }, 1000)

  await wait(FACE_REGISTER_COUNTDOWN_S * 1000)
  locked.value = false
  router.push({ name: 'face-register' })
}

/* ---------------- voice ---------------- */

async function startVoice() {
  if (!session.voiceAvailable) {
    chat.addQuietude(
      'Voice chat is unavailable - my local speech models are not loaded. ' +
      `${session.speech.detail}\n\nText chat works normally.`,
      'error',
    )
    return
  }
  const started = await voice.start()
  // .value matters here: this is JS, not a template, so a bare ref is
  // always truthy and stringifies to "[object Object]" - which would
  // replace the actual reason (a denied mic permission, usually) with
  // noise in the one message meant to explain it.
  if (!started && voice.notice.value) {
    chat.addQuietude(`${voice.notice.value} Voice chat turned off.`, 'error')
  }
}

async function toggleVoice() {
  // Routed through Quietude rather than toggled directly, so the mic button
  // and the spoken command take exactly the same path and she always
  // gets to acknowledge it.
  await send(voiceOn.value ? 'turn off voice chat' : 'turn on voice chat')
}

/* ---------------- read a message aloud ---------------- */

async function speakMessage(message) {
  if (!tts.available.value) {
    chat.addQuietude("Voice output isn't available right now.", 'error')
    return
  }
  speakingMessageId.value = message.id
  await tts.speak(message.text)
  if (speakingMessageId.value === message.id) speakingMessageId.value = null
}

function stopSpeaking() {
  tts.stop()
  speakingMessageId.value = null
}

function togglePlayback() {
  if (tts.speaking.value) tts.pause()
  else tts.resume()
}

/* ---------------- attachments ---------------- */

async function onFilesPicked(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = '' // so picking the same file again still fires change
  if (!files.length) return

  chat.addFiles(files.map((f) => ({ name: f.name, size: f.size })))
  try {
    const data = await api.upload(files)
    if (data.reply) await addReply(data.reply)
  } catch (err) {
    chat.addQuietude(err.message, 'error')
  }
}

/* ---------------- shutdown ---------------- */

async function shutdown() {
  await voice.stop()
  // The shutdown screen asks the backend, once its chime has finished.
  // Asking here as well would start the teardown immediately and cut
  // the sound off - the bug this whole arrangement exists to fix.
  router.push({ name: 'shutdown' })
}

/* ---------------- entry ---------------- */

onMounted(async () => {
  // Re-read who she is. The store loads once before the app mounts,
  // which is before first-run setup has asked for her name - so without
  // this the first session after setup would show the right name
  // everywhere except the label on her replies.
  identity.load()
  await tts.refreshStatus()
  await session.refreshConversationMode()

  // Only greet on a genuine arrival, not on coming back from /commands
  // or the settings page - which in the reference were separate windows,
  // so returning to the console simply didn't happen.
  if (!chat.messages.length) {
    const first = router.currentRoute.value.query.first === '1'
    const name = session.displayName
    chat.addQuietude(
      first
        ? `Welcome aboard, ${name}! I'm ${identity.displayName}. `
          + 'Type a command to get started, or say "help".'
        : `Hello, ${name}. I'm ${identity.displayName}. `
          + 'Type a command to get started, or say "help".',
    )

    if (session.voiceAvailable) {
      chat.addQuietude(
        'Quick note: while voice chat is on, I automatically speak all of my responses ' +
        "out loud - that's tied to voice chat itself and can't be toggled separately. " +
        'If I\'m ever mid-sentence and you\'d like me to stop, just say "stop talking".',
      )
    } else {
      chat.addQuietude(
        `Voice features are unavailable this session - ${session.speech.detail} ` +
        'Everything else works normally.',
        'warning',
      )
    }

    await checkPostLoginFacePrompt()
  }

  focusInput()
})

async function checkPostLoginFacePrompt() {
  try {
    const data = await api.postLoginCheck()
    if (data.ok && data.prompt) {
      await wait(700)
      expectMode.value = data.prompt.expect || 'text'
      await addReply(data.prompt.text, data.prompt.status)
    }
  } catch {
    // Not critical - the same nag reappears next login.
  }
}
</script>

<template>
  <div class="screen main-screen">
    <TopBar
      :conversation-mode="session.conversationMode"
      :conversation-turns="session.conversationTurns"
      :voice-state="voice.state.value"
      :voice-available="session.voiceAvailable"
      :voice-busy="voice.starting.value"
      @toggle-voice="toggleVoice"
      @shutdown="shutdown"
    />

    <TtsPlaybackBar
      v-if="tts.active.value"
      :preview="tts.preview.value"
      :playing="tts.speaking.value"
      @toggle="togglePlayback"
      @stop="stopSpeaking"
    />

    <div class="main-body">
      <SidePanel
        :user-name="session.user?.name || '—'"
        :disabled="inputDisabled"
        @run="runSuggestion"
      />

      <HudPanel variant="chat-panel">
        <ChatLog
          :messages="chat.messages"
          show-actions
          :speaking-id="speakingMessageId"
          @speak="speakMessage"
          @stop-speaking="stopSpeaking"
        />

        <form class="input-row" @submit.prevent="submit">
          <span class="prompt-caret">&gt;</span>
          <!--
            A password is a single line by definition and a textarea
            cannot mask what you type, so the masked prompt keeps the
            plain field. The growing box covers every other case, which
            is all of them but one.
          -->
          <input
            v-if="inputType === 'password'"
            ref="inputEl"
            :value="displayValue"
            type="password"
            class="chat-input"
            autocomplete="off"
            :placeholder="placeholder"
            :disabled="inputDisabled"
            @input="input = $event.target.value"
          />
          <AutoTextarea
            v-else
            ref="inputEl"
            class="chat-input"
            :class="{
              'voice-locked': voiceOn,
              'voice-blurred': voice.state.value === 'asleep',
              'voice-text-red': voice.state.value === 'asleep',
              'voice-text-green': voice.state.value === 'awake',
            }"
            :model-value="displayValue"
            :placeholder="placeholder"
            :disabled="inputDisabled"
            :max-rows="CHAT_INPUT_MAX_ROWS"
            aria-label="Message Quietude"
            @update:model-value="input = $event"
            @submit="submit"
          />
          <input ref="fileEl" type="file" multiple hidden @change="onFilesPicked" />
          <button
            type="button"
            class="btn-attach"
            title="Attach files"
            :disabled="locked"
            @click="fileEl?.click()"
          >
            <svg viewBox="0 0 24 24" fill="none">
              <path
                d="M21 11.5 12.5 20a4.5 4.5 0 0 1-6.36-6.36l8.48-8.49a3 3 0 0 1 4.25 4.25l-8.49 8.49a1.5 1.5 0 0 1-2.12-2.12l7.07-7.07"
                stroke="currentColor" stroke-width="1.7"
                stroke-linecap="round" stroke-linejoin="round"
              />
            </svg>
          </button>
          <button type="submit" class="btn-send" :disabled="inputDisabled">SEND</button>
        </form>
      </HudPanel>
    </div>
  </div>
</template>
