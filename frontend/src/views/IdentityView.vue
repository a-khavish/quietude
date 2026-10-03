<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  IdentityView.vue
  Who the assistant is.

  Quietude is the application; the assistant inside it is whoever the
  user decides. This page is where that is decided.

  Everything here applies as it is typed. There is no Save button, and
  that is deliberate rather than fashionable: three of these four fields
  only change how she refers to herself, and watching the example
  sentence below rewrite itself as you type is a far better explanation
  of what the setting does than any label.

  The name is the exception worth being careful about, and the page says
  so. It is what the wake word is built from, so it has a mechanical
  consequence the others don't - and a wake-word model recognises a
  fixed vocabulary, which means a name it has never heard can never wake
  her, however clearly it is said, with nothing about the failure saying
  why. So the name is checked against the model's own lexicon while it
  is being typed, and the answer is shown here rather than discovered
  later.
-->
<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useIdentityStore } from '@/stores/identity'
import HudPanel from '@/components/HudPanel.vue'
import BackBar from '@/components/BackBar.vue'

const router = useRouter()
const identity = useIdentityStore()

const name = ref('')
const pronouns = ref('they')
const age = ref('')
const nationality = ref('')

const errors = ref({})
const saving = ref(false)
const saved = ref(false)
const checking = ref(false)

const PRONOUN_CHOICES = [
  { value: 'she', label: 'she / her' },
  { value: 'he', label: 'he / him' },
  { value: 'they', label: 'they / them' },
]

const wakePhrase = computed(() =>
  name.value.trim() ? `Hello ${name.value.trim()}` : '',
)

/** The example sentence under the form, written in her own voice. */
const example = computed(() => {
  const p = identity.pronouns
  const who = name.value.trim() || 'your assistant'
  const bits = [`I'm ${who}`]
  if (age.value) bits.push(`${age.value}`)
  if (nationality.value.trim()) bits.push(nationality.value.trim())
  const intro = bits.length > 1
    ? `${bits[0]}, ${bits.slice(1).join(', ')}.`
    : `${bits[0]}.`
  return `${intro} You can wake ${p.them} by saying "${wakePhrase.value || 'the wake phrase'}".`
})

let saveTimer = null
let checkTimer = null

/**
 * Pushes one field. Debounced, because this fires on every keystroke -
 * the alternative was a Save button, and a Save button is how you end
 * up not knowing whether a setting took.
 */
function push(field, value, delay = 400) {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(async () => {
    saving.value = true
    try {
      await identity.save({ [field]: value })
      errors.value = { ...errors.value, [field]: undefined }
      saved.value = true
      setTimeout(() => { saved.value = false }, 1400)
    } catch (err) {
      errors.value = { ...errors.value, ...(err.payload?.field_errors || {}) }
      if (!err.payload?.field_errors) {
        errors.value = { ...errors.value, [field]: err.message }
      }
    } finally {
      saving.value = false
    }
  }, delay)
}

/** Asks the wake-word model whether it knows this name. */
function checkWake(value) {
  clearTimeout(checkTimer)
  if (!value.trim()) {
    identity.wakeCheck = null
    return
  }
  checkTimer = setTimeout(async () => {
    checking.value = true
    try {
      const result = await api.checkWakeName(value.trim())
      identity.wakeCheck = { known: result.known, detail: result.detail }
    } catch {
      identity.wakeCheck = null
    } finally {
      checking.value = false
    }
  }, 550)
}

watch(name, (value) => { push('name', value.trim()); checkWake(value) })
watch(pronouns, (value) => push('pronouns', value, 0))
watch(age, (value) => push('age', value === '' ? null : value))
watch(nationality, (value) => push('nationality', value.trim()))

onMounted(async () => {
  await identity.load()
  name.value = identity.identity.name || ''
  pronouns.value = identity.identity.pronouns || 'they'
  age.value = identity.identity.age ?? ''
  nationality.value = identity.identity.nationality || ''
})

onBeforeUnmount(() => {
  clearTimeout(saveTimer)
  clearTimeout(checkTimer)
})
</script>

<template>
  <div class="commands-page">
    <main class="commands-wrap">
      <HudPanel variant="identity-panel">
        <BackBar label="Back to main interface"
                 @back="router.push({ name: 'console' })" />

        <div class="panel-header commands-header">
          <span class="eyebrow">YOUR ASSISTANT</span>
          <h1 class="wordmark small">WHO SHE IS</h1>
        </div>

        <p class="identity-intro">
          Quietude is the application. The assistant inside it is yours to define —
          she has no name until you give her one, and nothing here is a default
          someone else picked.
        </p>

        <div class="tts-field">
          <div class="tts-field-label-row">
            <label for="assistant-name">Name</label>
            <span v-if="saving" class="tts-field-value">saving…</span>
            <span v-else-if="saved" class="tts-field-value">saved</span>
          </div>
          <input
            id="assistant-name" v-model="name" class="identity-input" type="text"
            maxlength="32" autocomplete="off" spellcheck="false"
            placeholder="What would you like to call her?"
          />
          <p v-if="errors.name" class="tts-field-error">{{ errors.name }}</p>

          <!-- The thing worth knowing before you commit to a name. -->
          <div class="identity-wake">
            <p class="identity-wake-title">
              This is also her wake word.
            </p>
            <p class="tts-field-hint">
              With voice chat on, you'll wake her by saying
              <strong>“{{ wakePhrase || 'Hello …' }}”</strong>.
              <em>hey</em>, <em>ok</em> and <em>hi</em> work too. Her name on its
              own deliberately doesn't — a name that woke her every time it came
              up in conversation would be unusable.
            </p>
            <p v-if="checking" class="tts-field-hint">Checking the wake-word model…</p>
            <p
              v-else-if="identity.wakeCheck && identity.wakeCheck.known === true"
              class="identity-wake-ok"
            >
              ✓ {{ identity.wakeCheck.detail }}
            </p>
            <p
              v-else-if="identity.wakeCheck && identity.wakeCheck.known === false"
              class="identity-wake-bad"
            >
              ⚠ {{ identity.wakeCheck.detail }}
            </p>
            <p
              v-else-if="identity.wakeCheck && identity.wakeCheck.known === null"
              class="tts-field-hint"
            >
              {{ identity.wakeCheck.detail }}
            </p>
            <p class="tts-field-hint">
              The name changes everything she says about herself and what she listens
              for. It doesn't change your account, your data, or anything on disk.
            </p>
          </div>
        </div>

        <div class="tts-field">
          <div class="tts-field-label-row">
            <label>Pronouns</label>
          </div>
          <div class="identity-choices">
            <button
              v-for="choice in PRONOUN_CHOICES"
              :key="choice.value"
              type="button"
              class="identity-choice"
              :class="{ chosen: pronouns === choice.value }"
              @click="pronouns = choice.value"
            >
              {{ choice.label }}
            </button>
          </div>
          <p class="tts-field-hint">
            Changes how she refers to herself, everywhere, as soon as you pick one.
          </p>
          <p v-if="errors.pronouns" class="tts-field-error">{{ errors.pronouns }}</p>
        </div>

        <div class="identity-row">
          <div class="tts-field">
            <div class="tts-field-label-row">
              <label for="assistant-age">Age</label>
            </div>
            <input
              id="assistant-age" v-model="age" class="identity-input" type="number"
              min="1" max="200" placeholder="optional"
            />
            <p v-if="errors.age" class="tts-field-error">{{ errors.age }}</p>
          </div>

          <div class="tts-field">
            <div class="tts-field-label-row">
              <label for="assistant-nationality">Nationality</label>
            </div>
            <input
              id="assistant-nationality" v-model="nationality" class="identity-input"
              type="text" maxlength="48" placeholder="optional"
            />
            <p v-if="errors.nationality" class="tts-field-error">
              {{ errors.nationality }}
            </p>
          </div>
        </div>

        <p class="tts-field-hint">
          Age and nationality colour how she introduces herself and how she speaks in
          conversation mode. Neither leaves this machine.
        </p>

        <!-- The best explanation of these settings is watching them work. -->
        <div class="identity-preview">
          <p class="side-label">HOW SHE'LL INTRODUCE HERSELF</p>
          <p class="identity-preview-text">{{ example }}</p>
        </div>

        <p class="tts-field-hint settings-note">
          Everything here saves as you type it — there's no button to press, and
          nothing to leave half-applied. To change the voice she says it in, open
          <button type="button" class="voices-inline-link link-flush"
                  @click="router.push({ name: 'tts-settings' })">TTS settings</button>;
          to change what she listens with, open
          <button type="button" class="voices-inline-link link-flush"
                  @click="router.push({ name: 'models' })">speech models</button>.
        </p>
      </HudPanel>
    </main>
  </div>
</template>

<style scoped>
.identity-panel {
  --panel-pad-x: 42px; max-width: 680px; width: 100%; padding: 30px 42px 36px; }

.identity-intro {
  margin: 18px 0 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--text-muted);
}

.identity-input {
  width: 100%;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 11px 12px;
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 13.5px;
  outline: none;
}
.identity-input:focus {
  border-color: var(--cyan);
  box-shadow: 0 0 12px rgba(45, 226, 230, 0.25);
}
.identity-input::placeholder { color: var(--text-muted); }

.identity-choices { display: flex; gap: 8px; flex-wrap: wrap; }
.identity-choice {
  flex: 1;
  min-width: 120px;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 12.5px;
  cursor: pointer;
  transition: 0.15s ease;
}
.identity-choice:hover { color: var(--cyan-bright); border-color: var(--cyan); }
.identity-choice.chosen {
  color: #05080d;
  background: var(--cyan);
  border-color: var(--cyan);
  font-weight: 600;
}

.identity-row { display: flex; gap: 16px; }
.identity-row .tts-field { flex: 1; }

/* The wake-word note is set apart because it is the one thing on this
   page with a consequence beyond wording. */
.identity-wake {
  margin-top: 12px;
  padding: 11px 13px;
  border: 1px solid rgba(45, 226, 230, 0.3);
  border-left-width: 3px;
  border-radius: 4px;
  background: rgba(45, 226, 230, 0.05);
}
.identity-wake-title {
  margin: 0 0 4px;
  font-size: 11.5px;
  letter-spacing: 0.04em;
  color: var(--cyan-bright);
}
.identity-wake-ok {
  margin: 6px 0 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: #3dff9a;
}
.identity-wake-bad {
  margin: 6px 0 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--warning);
}

.identity-preview {
  margin-top: 24px;
  padding: 14px 16px;
  border: 1px dashed var(--border);
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.02);
}
.identity-preview-text {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.75;
  color: var(--text);
}
.settings-note { margin-top: 22px; line-height: 1.75; }
</style>
