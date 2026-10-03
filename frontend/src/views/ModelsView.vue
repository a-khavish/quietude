<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  ModelsView.vue
  The speech model chooser.

  The voice library's sibling. That page picks the voice Quietude speaks
  in; this one picks the two models she listens with - the small vosk
  model that watches for the wake word, and the faster-whisper model
  that turns an utterance into words.

  Both have always been whatever install.sh downloaded, and changing
  either meant an environment variable and a re-run of the installer.
  The defaults are reasonable and they are also a compromise nobody got
  to make: the small wake model mishears unusual names, and the base
  transcription model is quick on a laptop and slow on a Pi.

  Grouped by role rather than listed flat, because these are two
  independent choices and a single list would read as eleven
  alternatives to one thing. Each group says what it is currently using
  before it offers anything, since "which one am I on" is the question
  someone opens this page with.

  One thing this page has to say that the voice library doesn't: a model
  is loaded once, into the speech worker, at launch. Choosing a
  different one takes effect at the next start, and the page says so
  every time rather than leaving someone to wonder why nothing changed.

  Markup follows the same .commands-page / .commands-wrap shell as the
  other full-screen pages, so the stylesheet's rules for them and for
  the sticky back bar apply unchanged. Everything specific to this page
  is in the scoped block at the bottom.
-->
<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
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
const restartNeeded = ref(false)

const models = ref([])
const roles = ref({})
const roleOrder = ref([])
const total = ref(0)
const showing = ref(0)

const query = ref('')
const roleFilter = ref('')
const busy = ref('')

/* key -> job, for everything currently downloading. Polled rather than
   pushed, for the same reason the voice library polls: a download is
   one of the few things in Quietude that takes minutes, a second of
   latency on a progress bar costs nothing, and a websocket for this
   alone would be a whole transport to maintain. */
const jobs = ref({})
let poll = null

/* The flat list the backend returns, cut into its two groups. Done here
   rather than asking for each role separately: it is one request either
   way, and the search box has to filter across both. */
const groups = computed(() =>
  roleOrder.value
    .map((role) => ({
      role,
      state: roles.value[role] || {},
      rows: models.value.filter((m) => m.role === role),
    }))
    .filter((g) => g.rows.length),
)

function size(bytes, approx = false) {
  if (!bytes) return ''
  const gb = bytes / 1000 / 1000 / 1000
  const text = gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1000 / 1000)} MB`
  return approx ? `~${text}` : text
}

function jobFor(key) {
  return jobs.value[key]
}

function running(key) {
  const job = jobs.value[key]
  return !!job && (job.state === 'queued' || job.state === 'downloading')
}

function percent(job) {
  if (!job?.total || job.indeterminate) return 0
  return Math.min(100, Math.round((job.received / job.total) * 100))
}

async function load() {
  error.value = null
  try {
    const data = await api.speechModels({
      role: roleFilter.value || undefined,
      q: query.value || undefined,
    })
    models.value = data.models
    roles.value = data.roles
    roleOrder.value = data.role_order
    total.value = data.total
    showing.value = data.showing
    for (const m of data.models) {
      if (m.job) jobs.value[m.key] = m.job
    }
    ensurePolling()
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

let searchTimer = null
function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => load(), 250)
}

async function install(model) {
  notice.value = null
  error.value = null
  try {
    const { job } = await api.installModel(model.key)
    jobs.value = { ...jobs.value, [model.key]: job }
    ensurePolling()
  } catch (err) {
    error.value = err.message
  }
}

async function cancel(model) {
  const job = jobs.value[model.key]
  if (!job) return
  await api.cancelModelJob(job.id).catch(() => {})
}

async function use(model) {
  notice.value = null
  error.value = null
  busy.value = model.key
  try {
    const res = await api.selectModel(model.role, model.key)
    roles.value = res.roles
    restartNeeded.value = restartNeeded.value || res.restart_required
    notice.value = res.restart_required
      ? `${model.name} will be used from the next time you start the app.`
      : `${model.name} is already the one in use.`
    await load()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = ''
  }
}

async function remove(model) {
  notice.value = null
  error.value = null
  busy.value = model.key
  try {
    const res = await api.removeModel(model.key)
    roles.value = res.roles
    restartNeeded.value = true
    notice.value = res.reassigned_to
      ? `Removed ${model.name}. It was the one in use, so I've switched to ` +
        `${res.reassigned_to} instead.`
      : `Removed ${model.name}.`
    await load()
  } catch (err) {
    // The backend refuses to remove the last model a role has, and that
    // refusal is the useful part of the answer, not a failure.
    error.value = err.message
  } finally {
    busy.value = ''
  }
}

function ensurePolling() {
  const active = Object.values(jobs.value).some(
    (j) => j.state === 'queued' || j.state === 'downloading',
  )
  if (active && !poll) poll = setInterval(tick, 700)
  if (!active && poll) {
    clearInterval(poll)
    poll = null
  }
}

async function tick() {
  const active = Object.entries(jobs.value).filter(
    ([, j]) => j.state === 'queued' || j.state === 'downloading',
  )
  for (const [key, job] of active) {
    try {
      const { job: fresh } = await api.modelJob(job.id)
      jobs.value = { ...jobs.value, [key]: fresh }
      if (fresh.state === 'done') {
        notice.value = `${fresh.name} is installed.`
        await load()
      } else if (fresh.state === 'failed') {
        error.value = `${fresh.name} didn't download: ${fresh.error}`
      }
    } catch {
      // A job that has aged out of the backend's list is not an error
      // worth showing; the row just stops moving.
    }
  }
  ensurePolling()
}

function activeNote(group) {
  const state = group.state
  if (state.source === 'legacy') {
    return 'Using the model the installer downloaded. Nothing here is ' +
      'selected yet, and downloading one will switch to it.'
  }
  if (!state.active_key) return `No model installed - ${identity.displayName} cannot listen yet.`
  const row = models.value.find((m) => m.key === state.active_key)
  return `Using ${row ? row.name : state.active_key}.`
}

onMounted(() => load())
onBeforeUnmount(() => {
  clearTimeout(searchTimer)
  if (poll) clearInterval(poll)
})

function back() {
  router.push({ name: 'console' })
}
</script>

<template>
  <div class="commands-page">
    <main class="commands-wrap">
      <HudPanel variant="models-panel">
        <BackBar label="Back to main interface" @back="back" />

        <div class="panel-header commands-header">
          <span class="eyebrow">SPEECH MODELS</span>
          <h1 class="wordmark small">CHOOSE HOW I LISTEN</h1>
        </div>

        <p class="models-online-note">
          Downloading a model uses the internet. Everything else here is offline -
          nothing you say or type ever leaves this machine - and this page is the
          exception: it fetches model files from two hosts and sends nothing about you.
          Once a model is downloaded it is used entirely on this machine.
        </p>

        <div class="models-controls">
          <input
            v-model="query" class="models-search" type="search"
            placeholder="Search by name, language or description..."
            aria-label="Search speech models"
            @input="onSearch"
          />
          <select v-model="roleFilter" class="models-select" aria-label="Kind of model"
                  @change="load()">
            <option value="">Both kinds</option>
            <option value="wake">Wake word</option>
            <option value="transcribe">Transcription</option>
          </select>
        </div>

        <div class="models-meta">
          <span class="models-count">{{ showing }} of {{ total }} models</span>
        </div>

        <p v-if="error" class="tts-field-error tts-general-error models-error">
          {{ error }}
        </p>
        <p v-if="notice" class="models-notice">{{ notice }}</p>
        <p v-if="restartNeeded" class="models-restart">
          A speech model is loaded once, at startup. Your change takes effect
          the next time she does.
        </p>

        <p v-if="loading" class="tts-field-hint">Looking at what's installed...</p>
        <p v-else-if="!groups.length" class="tts-field-hint">
          No models match that. Try clearing the search.
        </p>

        <section v-for="group in groups" :key="group.role" class="models-group">
          <header class="models-group-head">
            <h2 class="models-group-title">{{ group.state.label || group.role }}</h2>
            <p class="models-group-active" :class="{ none: !group.state.active_key
                                                      && group.state.source !== 'legacy' }">
              {{ activeNote(group) }}
            </p>
          </header>

          <ul class="model-list">
            <li
              v-for="m in group.rows" :key="m.key" class="model-row"
              :class="{ installed: m.installed, active: m.active }"
            >
              <div class="model-row-main">
                <p class="model-row-name">
                  {{ m.name }}
                  <span v-if="m.active" class="model-row-badge">in use</span>
                  <span v-else-if="m.installed" class="model-row-tag">installed</span>
                  <span v-if="m.default" class="model-row-tag">default</span>
                </p>
                <p class="model-row-sub">
                  {{ m.key }}
                  <template v-if="m.language"> · {{ m.language }}</template>
                  <template v-if="m.size_bytes">
                    · {{ size(m.size_bytes, m.size_approx) }}
                  </template>
                </p>
                <p v-if="m.description" class="model-row-note">{{ m.description }}</p>

                <div v-if="running(m.key)" class="model-progress">
                  <div class="model-progress-bar"
                       :class="{ indeterminate: jobFor(m.key).indeterminate }">
                    <div class="model-progress-fill"
                         :style="{ width: `${percent(jobFor(m.key))}%` }" />
                  </div>
                  <span class="model-progress-text">
                    <template v-if="jobFor(m.key).indeterminate">
                      downloading...
                    </template>
                    <template v-else>
                      {{ percent(jobFor(m.key)) }}% ·
                      {{ size(jobFor(m.key).received) }} of
                      {{ size(jobFor(m.key).total) }}
                    </template>
                  </span>
                </div>
              </div>

              <div class="model-row-actions">
                <button
                  v-if="running(m.key)" type="button" class="btn-ghost"
                  @click="cancel(m)"
                >
                  Cancel
                </button>
                <template v-else-if="m.installed">
                  <button
                    v-if="!m.active" type="button" class="btn-ghost"
                    :disabled="busy === m.key" @click="use(m)"
                  >
                    Use this
                  </button>
                  <button
                    type="button" class="btn-ghost danger"
                    :disabled="busy === m.key" @click="remove(m)"
                  >
                    Remove
                  </button>
                </template>
                <button
                  v-else type="button" class="btn-ghost" @click="install(m)"
                >
                  Download
                </button>
              </div>
            </li>
          </ul>
        </section>
      </HudPanel>
    </main>
  </div>
</template>

<style scoped>
/* Wider than the voice library's panel: a row here carries a sentence
   about what the model costs, and that sentence is the whole reason
   anyone can choose between them - wrapping it onto three lines would
   make the list unreadable. */
.models-panel {
  --panel-pad-x: 42px;
  width: 100%;
  max-width: 900px;
  padding: 30px 42px 36px;
}

/* The note has to be noticed - it is the one place Quietude's central
   promise has an exception - without shouting over the page. Same
   treatment as the voice library's, since it is the same exception. */
.models-online-note {
  margin: 18px 0 0;
  padding: 10px 13px;
  border: 1px solid rgba(255, 176, 32, 0.4);
  border-left-width: 3px;
  border-radius: 4px;
  background: rgba(255, 176, 32, 0.06);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--text-muted);
}

.models-controls {
  display: flex;
  gap: 10px;
  margin-top: 20px;
  flex-wrap: wrap;
}

.models-search {
  flex: 1;
  min-width: 200px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.03);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 13px;
  outline: none;
}
.models-search:focus {
  border-color: var(--cyan);
  box-shadow: 0 0 12px rgba(45, 226, 230, 0.25);
}
.models-search::placeholder { color: var(--text-muted); }

.models-select {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--panel-solid);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 13px;
  outline: none;
  cursor: pointer;
}
.models-select:focus { border-color: var(--cyan); }

.models-meta { margin-top: 12px; }
.models-count { font-size: 11.5px; color: var(--text-muted); }

.models-error { margin: 14px 0 0; }

.models-notice {
  margin: 14px 0 0;
  padding: 10px 13px;
  border: 1px solid rgba(45, 226, 230, 0.3);
  border-radius: 4px;
  background: rgba(45, 226, 230, 0.06);
  font-size: 12px;
  line-height: 1.7;
  color: var(--text);
}

/* Deliberately a different colour to the notice above it: "done" and
   "done, but not yet in effect" are different things, and someone who
   skims the cyan box and leaves would have the wrong idea about which. */
.models-restart {
  margin: 10px 0 0;
  padding: 10px 13px;
  border: 1px solid rgba(255, 176, 32, 0.4);
  border-left-width: 3px;
  border-radius: 4px;
  background: rgba(255, 176, 32, 0.06);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--text);
}

.models-group { margin-top: 26px; }

.models-group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}

.models-group-title {
  margin: 0;
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 2.5px;
  text-transform: uppercase;
  color: var(--cyan-bright);
}

.models-group-active {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-muted);
}
.models-group-active.none { color: var(--warning); }

.model-list {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.model-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 11px 13px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.02);
}
.model-row:hover { border-color: var(--border); }
.model-row.installed { background: rgba(45, 226, 230, 0.05); }
/* The one in use gets a left edge rather than a stronger fill: a whole
   row of brighter background reads as "selected" in a list where
   several rows are already tinted for being installed. */
.model-row.active {
  border-color: var(--border);
  box-shadow: inset 3px 0 0 var(--cyan);
}

.model-row-main { min-width: 0; flex: 1; }

.model-row-name {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 13.5px;
  color: var(--text);
}

.model-row-badge {
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--cyan);
  color: #05080d;
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.model-row-tag {
  padding: 1px 5px;
  border: 1px solid var(--border);
  border-radius: 3px;
  color: var(--cyan-bright);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

/* The key is shown as well as the name because it is what the model is
   actually called everywhere else - in install.sh, on alphacephei, on
   HuggingFace - and someone comparing this list against a README needs
   to be able to match them up. */
.model-row-sub {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-muted);
  overflow-wrap: anywhere;
}

.model-row-note {
  margin: 4px 0 0;
  max-width: 62ch;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-muted);
  opacity: 0.85;
}

.model-row-actions {
  flex-shrink: 0;
  display: flex;
  gap: 8px;
}
.model-row-actions .btn-ghost { padding: 9px 16px; font-size: 10px; }
.model-row-actions .btn-ghost:disabled { opacity: 0.5; cursor: default; }
.model-row-actions .btn-ghost.danger {
  border-color: rgba(255, 84, 112, 0.45);
  color: var(--danger);
}
.model-row-actions .btn-ghost.danger:hover {
  border-color: var(--danger);
  box-shadow: 0 0 10px rgba(255, 84, 112, 0.25);
}

.model-progress { display: flex; align-items: center; gap: 10px; margin-top: 8px; }

.model-progress-bar {
  flex: 1;
  height: 4px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}

.model-progress-fill {
  height: 100%;
  border-radius: 2px;
  background: var(--cyan);
  transition: width 0.3s ease;
}

/* huggingface_hub does its own downloading and reports no byte counts
   worth relying on, so for those installs there is no percentage to
   show. A bar frozen at zero would read as a stalled download, which is
   worse than admitting the progress isn't known. */
.model-progress-bar.indeterminate {
  background: linear-gradient(
    90deg,
    rgba(45, 226, 230, 0.08) 0%,
    rgba(45, 226, 230, 0.55) 50%,
    rgba(45, 226, 230, 0.08) 100%
  );
  background-size: 220% 100%;
  animation: model-sweep 1.4s linear infinite;
}
.model-progress-bar.indeterminate .model-progress-fill { display: none; }

@keyframes model-sweep {
  from { background-position: 100% 0; }
  to { background-position: -120% 0; }
}

.model-progress-text {
  font-size: 10.5px;
  color: var(--text-muted);
  white-space: nowrap;
}

/* 920, not 720: the window floor is 760, so the old value was below
   anything that can happen and this view had no responsive behaviour at
   all in the range it actually runs in. */
@media (max-width: 920px) {
  .models-panel { --panel-pad-x: 18px; padding: 24px 18px 28px; }
  .model-row { flex-direction: column; align-items: stretch; }
  .model-row-actions { justify-content: flex-end; }
}
</style>
