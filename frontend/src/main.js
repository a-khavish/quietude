/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * main.js
 * Mounts the app. That's all.
 *
 * The file this replaces was 1,700 lines: every screen transition, the
 * voice state machine, two camera loops, the TTS playback bar, DOM
 * construction for every chat bubble, and a heartbeat ping. It's worth
 * naming where all of that went, since "rebuilt as a Vue SPA" otherwise
 * says nothing about the structure:
 *
 *   screen transitions    router/index.js, with a guard instead of
 *                         showScreen() toggling a .hidden class
 *   voice state machine   composables/useVoiceChat.js
 *   speech recognition    composables/useSpeechEngine.js, replacing the
 *                         Web Speech API and its restart juggling
 *   camera loops          composables/useCamera.js + the two face views
 *   TTS                   composables/useTts.js, now an <audio> element
 *                         rather than four routes and a polling loop
 *   chat bubbles          stores/chat.js (data) + ChatBubble.vue (view)
 *   boot overlay          composables/useBoot.js
 *   chimes                composables/useChimes.js
 *   heartbeat             deleted - see api/system.py for why
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { router } from '@/router'
import App from '@/App.vue'
import { useIdentityStore } from '@/stores/identity'

import '@/styles/fonts.css'
import '@/styles/hud.css'

const app = createApp(App)
app.use(createPinia()).use(router)

// Who she is, before the first paint. The assistant's name is the label
// on every reply and the window title, and both are read from a CSS
// custom property and document.title rather than from a binding - so
// they need setting once, early, rather than being re-rendered into
// place a moment after the interface appears.
const identity = useIdentityStore()
identity.applyToDocument()
identity.load()

app.mount('#app')
