<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  TtsSettingsView.vue
  Voice customization: rate, volume, voice.

  Markup mirrors templates/tts_settings.html from the Windows build so
  the original stylesheet's rules for this page apply unchanged -
  including the check-mark voice list, which an earlier pass had replaced
  with a plain <select>, leaving that whole section of the stylesheet
  unused and the page looking nothing like the original.

  Two behaviours carried over exactly, because they're promises the old
  UI made in writing: nothing is applied until Save is pressed - leaving
  without saving keeps everything as it was - and "Reset to Default" only
  resets the form on screen, still requiring a Save to take effect.

  On success the original posted a message to its opener window and
  closed itself. There is no opener now, so it routes back to the console
  and drops the confirmation into the chat log directly.
-->
<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useChatStore } from '@/stores/chat'
import { useSharedTts } from '@/composables/useTts'
import HudPanel from '@/components/HudPanel.vue'
import BackBar from '@/components/BackBar.vue'
import AutoTextarea from '@/components/AutoTextarea.vue'
import { useIdentityStore } from '@/stores/identity'

const router = useRouter()
const chat = useChatStore()
const tts = useSharedTts()
const identity = useIdentityStore()

const loading = ref(true)
const saving = ref(false)
const loadError = ref(null)
const fieldErrors = ref({})
const generalError = ref(null)

const voices = ref([])
const defaults = ref({ rate: 200, volume: 1.0, voice_id: null })
const rateMin = ref(50)
const rateMax = ref(400)

const rate = ref(200)
const volume = ref(1.0)
const voiceId = ref('')

// Shown as the placeholder and used when the box is left empty, so
// what you read in the empty box is exactly what you hear if you just
// press the button.
const PREVIEW_SUGGESTION = computed(
  () => `Hello, I'm ${identity.displayName}. This is how I sound right now.`,
)

// Four lines: enough to test how she handles a couple of sentences
// running together, without the box swallowing the controls above it.
const PREVIEW_MAX_ROWS = 4

const previewText = ref('')

const volumePct = computed(() => Math.round(volume.value * 100))
const defaultVolumePct = computed(() => Math.round((defaults.value.volume ?? 1) * 100))

onMounted(async () => {
  try {
    const data = await api.ttsSettings()
    voices.value = data.voices
    defaults.value = data.defaults
    rateMin.value = data.rate_min
    rateMax.value = data.rate_max

    const base = data.saved || data.defaults
    rate.value = base.rate ?? data.defaults.rate
    volume.value = base.volume ?? data.defaults.volume
    voiceId.value = base.voice_id || data.defaults.voice_id || data.voices[0]?.id || ''
  } catch (err) {
    loadError.value = err.message
  } finally {
    loading.value = false
  }
})

function selectVoice(id) {
  voiceId.value = id
  fieldErrors.value = { ...fieldErrors.value, voice: undefined }
  // Warm it now rather than when Preview is pressed. Loading a voice
  // model costs most of a second, and the gap between clicking a voice
  // and hearing it is exactly where that second is most noticeable.
  api.preloadVoice(id).catch(() => {})
}

/** Resets the form only. Still needs a Save to actually take effect. */
function resetToDefaults() {
  rate.value = defaults.value.rate
  volume.value = defaults.value.volume
  if (defaults.value.voice_id) voiceId.value = defaults.value.voice_id
  else if (voices.value.length) voiceId.value = voices.value[0].id
  fieldErrors.value = {}
  generalError.value = null
}

async function save() {
  saving.value = true
  fieldErrors.value = {}
  generalError.value = null
  try {
    await api.saveTtsSettings({
      rate: rate.value,
      volume: volume.value,
      voice_id: voiceId.value,
    })
    chat.addQuietude('TTS configuration has been saved successfully and applied.', 'success')
    router.push({ name: 'console' })
  } catch (err) {
    // The backend validates per field and says which failed, so the
    // message lands on the right control rather than as one generic line.
    if (err.payload?.field_errors) fieldErrors.value = err.payload.field_errors
    else generalError.value = err.message
  } finally {
    saving.value = false
  }
}

/**
 * Plays the text in the box using the controls as they currently read,
 * not as they were last saved.
 *
 * It used to preview the saved settings, because synthesis read them
 * off disk - which meant the one control that exists to answer "what
 * will this sound like?" couldn't answer it until after you had
 * committed to the change. /api/tts/speak now takes the three values
 * with the text, so the preview is of what is on screen. Nothing is
 * written: previewing a voice you then navigate away from leaves the
 * saved settings untouched.
 */
function preview() {
  tts.speak(previewText.value.trim() || PREVIEW_SUGGESTION.value, {
    rate: rate.value,
    volume: volume.value,
    voice_id: voiceId.value,
  })
}

/** One button, because "Preview" that cannot be stopped is an unkind
 *  button to put next to a box you can paste three paragraphs into. */
function togglePreview() {
  if (tts.active.value) tts.stop()
  else preview()
}

// Volume is the one setting that can change mid-sentence, since it's a
// property of the audio element rather than part of the clip. Dragging
// the slider during a preview is heard immediately, which is what you
// want from a volume control you are in the middle of testing.
watch(volume, (v) => tts.setVolume(v))

// Picking a different voice while one is playing: stop, rather than
// leave the old voice talking over a changed selection. Rate is the
// same - neither can be applied to a clip that already exists.
watch([voiceId, rate], () => {
  if (tts.active.value) tts.stop()
})
</script>

<template>
  <div class="commands-page">
    <main class="commands-wrap">
      <HudPanel variant="tts-settings-panel">
        <BackBar label="Back to main interface"
                 @back="router.push({ name: 'console' })" />

        <div class="panel-header commands-header">
          <span class="eyebrow">VOICE CUSTOMIZATION</span>
          <h1 class="wordmark small">TTS SETTINGS</h1>
        </div>

        <p v-if="generalError || loadError" class="tts-field-error tts-general-error">
          {{ generalError || loadError }}
        </p>

        <template v-if="!loading && !loadError">
          <div class="tts-field">
            <div class="tts-field-label-row">
              <label for="tts-rate">Speech Rate</label>
              <span class="tts-field-value">{{ rate }} wpm</span>
            </div>
            <input
              id="tts-rate" v-model.number="rate" type="range"
              :min="rateMin" :max="rateMax" step="1"
            />
            <p class="tts-field-hint">
              Words per minute, {{ rateMin }}-{{ rateMax }} (default {{ defaults.rate }}).
            </p>
            <p v-if="fieldErrors.rate" class="tts-field-error">{{ fieldErrors.rate }}</p>
          </div>

          <div class="tts-field">
            <div class="tts-field-label-row">
              <label for="tts-volume">Volume</label>
              <span class="tts-field-value">{{ volumePct }}%</span>
            </div>
            <input
              id="tts-volume" v-model.number="volume" type="range"
              min="0" max="1" step="0.05"
            />
            <p class="tts-field-hint">0 to 100% (default {{ defaultVolumePct }}%).</p>
            <p v-if="fieldErrors.volume" class="tts-field-error">{{ fieldErrors.volume }}</p>
          </div>

          <div class="tts-field">
            <div class="tts-field-label-row">
              <label>Voice</label>
            </div>
            <div class="tts-voice-list">
              <div
                v-for="v in voices"
                :key="v.id"
                class="voice-option"
                :class="{ checked: v.id === voiceId }"
                @click="selectVoice(v.id)"
              >
                <span class="voice-check" />
                <span class="voice-option-name">{{ v.name }}</span>
              </div>
            </div>
            <p v-if="!voices.length" class="tts-field-hint">
              No voices were detected. Open the voice library below to download one, or
              install <code>espeak-ng</code> for a basic fallback.
            </p>
            <p class="tts-field-hint">
              <button type="button" class="voices-inline-link link-flush"
                      @click="router.push({ name: 'voices' })">
                Browse and download more voices
              </button>
              — around a thousand of them, in sixty-odd languages.
            </p>
            <p v-if="fieldErrors.voice" class="tts-field-error">{{ fieldErrors.voice }}</p>
          </div>

          <div class="tts-field">
            <div class="tts-field-label-row">
              <label for="tts-preview">Preview</label>
            </div>
            <div class="tts-preview-row">
              <AutoTextarea
                id="tts-preview"
                v-model="previewText"
                class="tts-preview-box"
                :placeholder="PREVIEW_SUGGESTION"
                :max-rows="PREVIEW_MAX_ROWS"
                :submit-on-enter="false"
                aria-label="Text to preview"
              />
              <button
                type="button" class="btn-ghost"
                :disabled="!tts.available.value" @click="togglePreview"
              >
                {{ tts.active.value ? 'Stop' : 'Preview' }}
              </button>
            </div>
            <p class="tts-field-hint">
              Played in the voice, rate and volume set above as they read now — no
              need to save first. Leave it empty to hear the suggested line.
            </p>
          </div>

          <p class="tts-field-hint settings-note">
            Previewing changes nothing. Nothing is applied until you press Save Changes:
            leaving without saving keeps everything as it was, and Reset to Default only
            resets the form.
          </p>

          <div class="tts-settings-actions">
            <button type="button" class="btn-ghost" @click="resetToDefaults">
              Reset to Default
            </button>
            <button type="button" class="btn-primary" :disabled="saving" @click="save">
              {{ saving ? 'Saving…' : 'Save Changes' }}
            </button>
          </div>
        </template>

        <p v-else-if="loading" class="tts-field-hint">Loading voices...</p>
      </HudPanel>
    </main>
  </div>
</template>

<style scoped>
/* The voice list scrolls, so this note needs clear separation from it -
   otherwise it reads as the next row of a clipped list. */
.settings-note { margin-top: 22px; line-height: 1.65; }
</style>
