<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  VoicesView.vue
  The voice library.

  Until now Quietude had whichever voice install.sh downloaded, and changing
  it meant an environment variable and a re-run of the installer. There
  are around a thousand piper voices across sixty-odd languages; this is
  where you get them.

  This is one of only two screens in Quietude that use the internet, and it says so
  rather than quietly doing it. Nothing about the user is sent - it
  fetches a list and downloads files from one host - but "nothing leaves
  this machine" is the promise the whole project is built on, and the
  honest thing is to mark the exception where someone will see it.

  Markup follows the same .commands-page / .commands-wrap shell as the
  other two full-screen pages so the stylesheet's rules for them apply
  unchanged, and the sticky back bar works the same way.
-->
<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useChatStore } from '@/stores/chat'
import HudPanel from '@/components/HudPanel.vue'
import BackBar from '@/components/BackBar.vue'

const router = useRouter()
const chat = useChatStore()

const loading = ref(true)
const refreshing = ref(false)
const error = ref(null)
const notice = ref(null)
const source = ref(null)

const voices = ref([])
const languages = ref([])
const total = ref(0)
const showing = ref(0)

const language = ref('')
const query = ref('')
const installedOnly = ref(false)

/* key -> job, for everything currently downloading. Polled rather than
   pushed: a download is the one thing in Quietude that takes minutes, and a
   second of latency on a progress bar costs nothing, where a websocket
   for this alone would be a whole transport to maintain. */
const jobs = ref({})
let poll = null

const rows = computed(() =>
  installedOnly.value ? voices.value.filter((v) => v.installed) : voices.value,
)

const installedCount = computed(() => voices.value.filter((v) => v.installed).length)

function mb(bytes) {
  if (!bytes) return ''
  return `${(bytes / 1024 / 1024).toFixed(bytes > 10 * 1024 * 1024 ? 0 : 1)} MB`
}

async function load({ refresh = false } = {}) {
  if (refresh) refreshing.value = true
  error.value = null
  try {
    const data = await api.voices({
      refresh: refresh ? 1 : undefined,
      language: language.value || undefined,
      q: query.value || undefined,
    })
    voices.value = data.voices
    languages.value = data.languages
    total.value = data.total
    showing.value = data.showing
    source.value = data.source
    // A catalog that couldn't be refreshed but was served from cache is
    // worth saying out loud - the list is real, it's just not today's.
    error.value = data.error
    for (const v of data.voices) {
      if (v.job) jobs.value[v.key] = v.job
    }
    ensurePolling()
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

let searchTimer = null
function onSearch() {
  clearTimeout(searchTimer)
  // Filtering happens on the backend over a thousand-entry list; typing
  // "german" shouldn't be six round trips.
  searchTimer = setTimeout(() => load(), 250)
}

async function install(voice) {
  notice.value = null
  try {
    const { job } = await api.installVoice(voice.key)
    jobs.value = { ...jobs.value, [voice.key]: job }
    ensurePolling()
  } catch (err) {
    error.value = err.message
  }
}

async function cancel(voice) {
  const job = jobs.value[voice.key]
  if (!job) return
  await api.cancelVoiceJob(job.id).catch(() => {})
}

async function remove(voice) {
  notice.value = null
  try {
    const res = await api.removeVoice(voice.key)
    voice.installed = false
    notice.value = res.reassigned_to
      ? `Removed ${voice.key}. It was your selected voice, so I've switched to another one.`
      : `Removed ${voice.key}.`
    await load()
  } catch (err) {
    error.value = err.message
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
      const { job: fresh } = await api.voiceJob(job.id)
      jobs.value = { ...jobs.value, [key]: fresh }
      if (fresh.state === 'done') {
        const voice = voices.value.find((v) => v.key === key)
        if (voice) voice.installed = true
        notice.value = `${key} is installed and loaded - pick it in TTS settings.`
      } else if (fresh.state === 'failed') {
        error.value = `${key} didn't download: ${fresh.error}`
      }
    } catch {
      // A job that has aged out of the backend's list is not an error
      // worth showing; the row just stops moving.
    }
  }
  ensurePolling()
}

function percent(job) {
  if (!job?.total) return 0
  return Math.min(100, Math.round((job.received / job.total) * 100))
}

onMounted(() => load())
onBeforeUnmount(() => {
  clearTimeout(searchTimer)
  if (poll) clearInterval(poll)
})

function back() {
  router.push({ name: 'console' })
}

function openTtsSettings() {
  chat.addQuietude('Opening voice settings.')
  router.push({ name: 'tts-settings' })
}
</script>

<template>
  <div class="commands-page">
    <main class="commands-wrap">
      <HudPanel variant="voices-panel">
        <BackBar label="Back to main interface" @back="back" />

        <div class="panel-header commands-header">
          <span class="eyebrow">VOICE LIBRARY</span>
          <h1 class="wordmark small">DOWNLOAD VOICES</h1>
        </div>

        <p class="voices-online-note">
          This screen and the speech models page are the only two here that use
          the internet. This one fetches the list of available voices and downloads the
          ones you pick, from one host. Nothing about you is sent, and nothing else in
          this app goes online.
        </p>

        <div class="voices-controls">
          <input
            v-model="query" class="voices-search" type="search"
            placeholder="Search by name, language or country..."
            aria-label="Search voices"
            @input="onSearch"
          />
          <select v-model="language" class="voices-select" aria-label="Language"
                  @change="load()">
            <option value="">All languages</option>
            <option v-for="l in languages" :key="l.code" :value="l.code">
              {{ l.language }}{{ l.country ? ` (${l.country})` : '' }}
            </option>
          </select>
          <button type="button" class="btn-ghost" :disabled="refreshing"
                  @click="load({ refresh: true })">
            {{ refreshing ? 'Refreshing…' : 'Refresh' }}
          </button>
        </div>

        <div class="voices-meta">
          <label class="voices-toggle">
            <input v-model="installedOnly" type="checkbox" />
            <span>Installed only ({{ installedCount }})</span>
          </label>
          <span v-if="!loading" class="voices-count">
            {{ showing }} of {{ total }} voices
            <template v-if="source === 'cache'"> · from a saved copy of the list</template>
          </span>
        </div>

        <p v-if="error" class="tts-field-error tts-general-error">{{ error }}</p>
        <p v-if="notice" class="voices-notice">
          {{ notice }}
          <button type="button" class="voices-inline-link" @click="openTtsSettings">
            Open TTS settings
          </button>
        </p>

        <p v-if="loading" class="tts-field-hint">Loading the voice catalog...</p>
        <p v-else-if="!rows.length" class="tts-field-hint">
          No voices match that. Try clearing the search, or Refresh if the list looks
          short.
        </p>

        <ul v-else class="voice-library">
          <li v-for="v in rows" :key="v.key" class="voice-row"
              :class="{ installed: v.installed }">
            <div class="voice-row-main">
              <p class="voice-row-name">
                {{ v.name }}
                <span v-if="v.quality" class="voice-row-quality">{{ v.quality }}</span>
                <span v-if="v.installed" class="voice-row-badge">installed</span>
              </p>
              <p class="voice-row-sub">
                {{ v.language }}<template v-if="v.country"> · {{ v.country }}</template>
                <template v-if="v.size_bytes"> · {{ mb(v.size_bytes) }}</template>
                <template v-if="v.num_speakers > 1">
                  · {{ v.num_speakers }} speakers
                </template>
              </p>
              <p v-if="v.quality_note" class="voice-row-note">{{ v.quality_note }}</p>

              <div v-if="jobs[v.key] && ['queued', 'downloading'].includes(jobs[v.key].state)"
                   class="voice-progress">
                <div class="voice-progress-bar">
                  <div class="voice-progress-fill"
                       :style="{ width: `${percent(jobs[v.key])}%` }" />
                </div>
                <span class="voice-progress-text">
                  {{ percent(jobs[v.key]) }}% · {{ mb(jobs[v.key].received) }}
                  of {{ mb(jobs[v.key].total) }}
                </span>
              </div>
            </div>

            <div class="voice-row-actions">
              <button
                v-if="jobs[v.key] && ['queued', 'downloading'].includes(jobs[v.key].state)"
                type="button" class="btn-ghost" @click="cancel(v)"
              >
                Cancel
              </button>
              <button
                v-else-if="v.installed" type="button" class="btn-ghost danger"
                @click="remove(v)"
              >
                Remove
              </button>
              <button v-else type="button" class="btn-ghost" @click="install(v)">
                Download
              </button>
            </div>
          </li>
        </ul>
      </HudPanel>
    </main>
  </div>
</template>

<style scoped>
/* The note has to be noticed - it is the one place Quietude's central
   promise has an exception - without shouting over the page. */
.voices-online-note {
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
</style>
