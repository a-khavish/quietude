<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  AppSettingsView.vue
  How the application behaves, as opposed to how she behaves.

  The other settings pages are about the assistant - her name, her
  voice, what she listens with. This one is about the window she lives
  in, which is the first thing people look for in a desktop application
  and the last thing this had.

  Two settings, and both of them are acted on by something that is not
  this page. Closing is handled by the window process, which asks the
  backend at the moment the close button is pressed. Starting at login
  is a file in ~/.config/autostart that the desktop reads when you log
  in. So neither has a live preview to offer, and both say plainly what
  will happen instead.

  Saved as you change them, like the identity page - there is no Save
  button because there is nothing to get wrong by forgetting it.
-->
<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useIdentityStore } from '@/stores/identity'
import HudPanel from '@/components/HudPanel.vue'
import BackBar from '@/components/BackBar.vue'

const router = useRouter()
const identity = useIdentityStore()

const loading = ref(true)
const error = ref(null)
const notice = ref(null)
const closeAction = ref('quit')
const startAtLogin = ref(false)

const CLOSE_CHOICES = [
  {
    value: 'quit',
    label: 'Quit',
    detail: 'Closing the window shuts everything down. Nothing keeps running.',
  },
  {
    value: 'tray',
    label: 'Keep running',
    detail: 'Closing the window hides it. She stays listening, and a tray '
          + 'icon brings her back.',
  },
]

async function load() {
  loading.value = true
  try {
    const data = await api.appSettings()
    closeAction.value = data.close_action
    startAtLogin.value = data.start_at_login
    error.value = null
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function save(changes) {
  notice.value = null
  try {
    const data = await api.saveAppSettings(changes)
    closeAction.value = data.close_action
    startAtLogin.value = data.start_at_login
    error.value = data.error || null
    if (!data.error) notice.value = 'Saved.'
  } catch (err) {
    error.value = err.message
    // Put the controls back to what is actually stored, rather than
    // leaving them showing a change that did not happen.
    await load()
  }
}

function chooseClose(value) {
  if (value === closeAction.value) return
  closeAction.value = value
  save({ close_action: value })
}

function toggleStartup() {
  const wanted = !startAtLogin.value
  startAtLogin.value = wanted
  save({ start_at_login: wanted })
}

function back() {
  router.push({ name: 'console' })
}

onMounted(load)
</script>

<template>
  <div class="commands-page">
    <main class="commands-wrap">
      <HudPanel variant="app-settings-panel">
        <BackBar label="Back to main interface" @back="back" />

        <div class="panel-header commands-header">
          <span class="eyebrow">THE APPLICATION</span>
          <h1 class="wordmark small">APP SETTINGS</h1>
        </div>

        <p v-if="loading" class="settings-hint">Loading…</p>

        <template v-else>
          <section class="settings-group">
            <h2 class="settings-label">WHEN YOU CLOSE THE WINDOW</h2>
            <div class="settings-choices">
              <button
                v-for="choice in CLOSE_CHOICES"
                :key="choice.value"
                type="button"
                class="settings-choice"
                :class="{ chosen: closeAction === choice.value }"
                @click="chooseClose(choice.value)"
              >
                <span class="settings-choice-name">{{ choice.label }}</span>
                <span class="settings-choice-detail">{{ choice.detail }}</span>
              </button>
            </div>
            <p class="settings-hint">
              Either way, <strong>shutdown</strong> always stops everything —
              that's what it's for.
            </p>
          </section>

          <section class="settings-group">
            <h2 class="settings-label">WHEN YOU LOG IN</h2>
            <button
              type="button"
              class="settings-switch"
              :class="{ on: startAtLogin }"
              role="switch"
              :aria-checked="startAtLogin"
              @click="toggleStartup"
            >
              <span class="settings-switch-track"><span class="settings-switch-knob" /></span>
              <span class="settings-switch-text">
                Start {{ identity.displayName }} when I log in
              </span>
            </button>
            <p class="settings-hint">
              Adds an entry to your desktop's own startup applications. You can
              remove it there too, and this page will show that.
            </p>
          </section>

          <p v-if="error" class="settings-error">{{ error }}</p>
          <p v-else-if="notice" class="settings-notice">{{ notice }}</p>
        </template>
      </HudPanel>
    </main>
  </div>
</template>

<style scoped>
.app-settings-panel {
  --panel-pad-x: 42px;
  width: 100%;
  max-width: 680px;
  padding: 30px 42px 36px;
}

.settings-group { margin-top: 26px; }

.settings-label {
  font-size: 10px;
  letter-spacing: 2px;
  color: var(--cyan-dim);
  margin: 0 0 10px;
  font-weight: 600;
}

/* Wraps rather than squeezing: at the narrow end two of these side by
   side would each be too narrow to read the explanation in, and the
   explanation is the part that matters. */
.settings-choices {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.settings-choice {
  flex: 1 1 240px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
  text-align: left;
  padding: 13px 15px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.02);
  color: var(--text-muted);
  font-family: var(--font-mono);
  cursor: pointer;
  transition: 0.15s ease;
}

.settings-choice:hover { border-color: var(--cyan-dim); }

.settings-choice.chosen {
  border-color: var(--cyan);
  background: rgba(45, 226, 230, 0.07);
  color: var(--text);
}

.settings-choice-name { font-size: 13.5px; font-weight: 600; }
.settings-choice.chosen .settings-choice-name { color: var(--cyan-bright); }
.settings-choice-detail { font-size: 12px; line-height: 1.5; }

.settings-switch {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 12px 15px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.02);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: 0.15s ease;
}

.settings-switch:hover { border-color: var(--cyan-dim); }
.settings-switch.on { border-color: var(--cyan); }

.settings-switch-track {
  flex: 0 0 auto;
  width: 38px;
  height: 20px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid var(--border);
  position: relative;
  transition: 0.18s ease;
}

.settings-switch.on .settings-switch-track {
  background: rgba(45, 226, 230, 0.3);
  border-color: var(--cyan);
}

.settings-switch-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--text-muted);
  transition: 0.18s ease;
}

.settings-switch.on .settings-switch-knob {
  left: 20px;
  background: var(--cyan-bright);
  box-shadow: 0 0 8px rgba(45, 226, 230, 0.7);
}

.settings-switch-text { flex: 1; min-width: 0; }

.settings-hint {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-muted);
  margin: 10px 0 0;
}

.settings-hint strong { color: var(--cyan-dim); font-weight: 600; }

.settings-error {
  margin: 20px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--red, #ff5470);
}

.settings-notice {
  margin: 20px 0 0;
  font-size: 12.5px;
  color: var(--cyan-dim);
}
</style>
