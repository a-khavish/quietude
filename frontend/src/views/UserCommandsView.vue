<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  UserCommandsView.vue
  Commands people write themselves.

  The command reference lists what she can already do. This is where you
  add to it: a name you type or say, a line about what it does, something
  to run on this machine, and three lines she says.

  The three lines land when they describe. Starting and running go out as
  the command launches; finished arrives when the process has actually
  exited, above whatever it printed. Which is why Run returns straight
  away and the page watches for the ending rather than waiting on it -
  a page frozen for the length of a backup cannot show you a Stop button.

  One form, used for both creating and modifying, because they are the
  same fields either way and two forms would drift apart. Only one is open
  at a time: a page with three half-finished forms on it is a page where
  you lose track of which one you were filling in.

  Selection is for deleting and nothing else, so it only appears once
  something is selected - a column of checkboxes beside a list you are
  mostly reading is noise.
-->
<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useIdentityStore } from '@/stores/identity'
import HudPanel from '@/components/HudPanel.vue'
import BackBar from '@/components/BackBar.vue'
import AutoTextarea from '@/components/AutoTextarea.vue'

const router = useRouter()
const identity = useIdentityStore()

const loading = ref(true)
const error = ref(null)
const notice = ref(null)
const commands = ref([])
const running = ref(null)
const directory = ref('')
const defaultTimeout = ref(300)

// The last finished command, and where the counter stood when the page
// opened - so a command that ran before you got here is not reported as
// having just happened.
const finished = ref(null)
let resultSeq = 0

const selected = ref(new Set())
const selectionCount = computed(() => selected.value.size)

/** null when no form is open, otherwise the slug being modified or ''
 *  for a new one. One at a time, deliberately. */
const editing = ref(null)
const saving = ref(false)
const fieldErrors = ref({})
const form = ref(blankForm())

function blankForm() {
  return {
    name: '',
    description: '',
    command: '',
    use_shell: false,
    say_starting: '',
    say_running: '',
    say_finished: '',
    timeout_seconds: 300,
  }
}

let pollTimer = null

/** Polls faster while something is running: a command that takes two
 *  seconds should not look finished for one of them. */
function pollRate() {
  return running.value ? 800 : 2500
}

function schedulePoll() {
  clearTimeout(pollTimer)
  pollTimer = setTimeout(async () => {
    await load({ quiet: true })
    schedulePoll()
  }, pollRate())
}

async function load({ quiet = false } = {}) {
  if (!quiet) loading.value = true
  try {
    const data = await api.userCommands()
    commands.value = data.commands
    running.value = data.running
    directory.value = data.directory
    defaultTimeout.value = data.default_timeout

    // What ended since the last look. The page shows it where the Run
    // button put its notice before, which is the same information in
    // the same place - only now it is the real ending rather than a
    // guess made before the command had run.
    const activity = await api.commandActivity(resultSeq)
    running.value = activity.running
    if (activity.finished) {
      resultSeq = activity.finished.result_seq
      finished.value = activity.finished
      notice.value = null
    }
    // Anything deleted elsewhere drops out of the selection rather than
    // sitting in it as a slug that no longer exists.
    const alive = new Set(data.commands.map((c) => c.slug))
    selected.value = new Set([...selected.value].filter((s) => alive.has(s)))
    if (!quiet) error.value = null
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function toggleSelected(slug) {
  const next = new Set(selected.value)
  if (next.has(slug)) next.delete(slug)
  else next.add(slug)
  selected.value = next
}

function selectAll() {
  selected.value = selected.value.size === commands.value.length
    ? new Set()
    : new Set(commands.value.map((c) => c.slug))
}

function startCreating() {
  form.value = blankForm()
  form.value.timeout_seconds = defaultTimeout.value
  fieldErrors.value = {}
  editing.value = ''
  notice.value = null
}

function startModifying(command) {
  form.value = { ...blankForm(), ...command }
  fieldErrors.value = {}
  editing.value = command.slug
  notice.value = null
}

function cancelForm() {
  editing.value = null
  fieldErrors.value = {}
}

async function submitForm() {
  saving.value = true
  fieldErrors.value = {}
  error.value = null
  try {
    const payload = { ...form.value }
    if (editing.value) await api.updateUserCommand(editing.value, payload)
    else await api.createUserCommand(payload)
    notice.value = editing.value ? 'Saved.' : 'Created.'
    editing.value = null
    await load({ quiet: true })
  } catch (err) {
    if (err.payload?.field_errors) fieldErrors.value = err.payload.field_errors
    else error.value = err.message
  } finally {
    saving.value = false
  }
}

async function deleteSelected() {
  if (!selectionCount.value) return
  const slugs = [...selected.value]
  error.value = null
  try {
    const data = await api.deleteUserCommands(slugs)
    if (data.error) error.value = data.error
    notice.value = `Deleted ${data.removed.length}.`
    selected.value = new Set()
    if (editing.value && slugs.includes(editing.value)) editing.value = null
    await load({ quiet: true })
  } catch (err) {
    error.value = err.message
  }
}

async function runCommand(command) {
  notice.value = null
  error.value = null
  finished.value = null
  // Shown immediately rather than waiting for the next poll to say so.
  running.value = { slug: command.slug, name: command.name,
                    say_running: command.say_running }
  try {
    await api.runUserCommand(command.slug)
  } catch (err) {
    error.value = err.message
    running.value = null
  }
  await load({ quiet: true })
  schedulePoll()
}

async function stopRunning() {
  try {
    await api.stopUserCommand()
  } catch (err) {
    error.value = err.message
  } finally {
    await load({ quiet: true })
  }
}

function back() {
  router.push({ name: 'console' })
}

onMounted(async () => {
  // Where the counter stands before anything is shown, so an ending
  // from an earlier visit is not announced as new.
  try {
    resultSeq = (await api.commandActivity(0)).result_seq || 0
  } catch {
    // The load below will report anything genuinely wrong.
  }
  await load()
  // Something may be running because it was said out loud rather than
  // started here, so the page watches rather than assuming it is the
  // only way in.
  schedulePoll()
})

onBeforeUnmount(() => clearTimeout(pollTimer))
</script>

<template>
  <div class="commands-page">
    <main class="commands-wrap">
      <HudPanel variant="user-commands-panel">
        <BackBar label="Back to main interface" @back="back" />

        <div class="panel-header commands-header">
          <span class="eyebrow">YOUR OWN</span>
          <h1 class="wordmark small">USER COMMANDS</h1>
          <p class="uc-intro">
            A name you type or say, what it does, something to run on this
            machine, and what {{ identity.displayName }} says while it runs.
            Only one runs at a time.
          </p>
        </div>

        <div v-if="running" class="uc-running btn-row compact">
          <span class="uc-running-dot" />
          <span class="uc-running-text">
            Running “{{ running.name }}”…
            <span v-if="running.say_running" class="uc-running-says">
              “{{ running.say_running }}”
            </span>
          </span>
          <button type="button" class="btn-ghost" @click="stopRunning">
            Stop
          </button>
        </div>

        <div class="uc-toolbar btn-row">
          <button
            type="button"
            class="btn-primary"
            :disabled="editing !== null"
            @click="startCreating"
          >
            New command
          </button>
          <template v-if="commands.length">
            <button type="button" class="btn-ghost" @click="selectAll">
              {{ selectionCount === commands.length ? 'Select none' : 'Select all' }}
            </button>
            <button
              v-if="selectionCount"
              type="button"
              class="btn-ghost danger-outline"
              @click="deleteSelected"
            >
              Delete {{ selectionCount }}
            </button>
          </template>
        </div>

        <!-- One form, for creating and for modifying. -->
        <form v-if="editing !== null" class="uc-form" @submit.prevent="submitForm">
          <h2 class="uc-form-title">
            {{ editing ? 'Modify command' : 'New command' }}
          </h2>

          <label class="uc-field">
            <span class="uc-label">NAME — what you type or say</span>
            <input
              v-model="form.name"
              type="text"
              class="uc-input"
              placeholder="back up my notes"
              autocomplete="off"
            />
            <span v-if="fieldErrors.name" class="uc-error">{{ fieldErrors.name }}</span>
          </label>

          <label class="uc-field">
            <span class="uc-label">DESCRIPTION — what it does</span>
            <input
              v-model="form.description"
              type="text"
              class="uc-input"
              placeholder="Copies this week's notes to the backup drive."
              autocomplete="off"
            />
            <span class="uc-hint">
              Shown beside it here and in the command reference. Optional.
            </span>
            <span v-if="fieldErrors.description" class="uc-error">
              {{ fieldErrors.description }}
            </span>
          </label>

          <label class="uc-field">
            <span class="uc-label">COMMAND — what runs on this machine</span>
            <AutoTextarea
              v-model="form.command"
              class="uc-input uc-mono"
              placeholder="rsync -a ~/notes/ ~/backups/notes/"
              :max-rows="5"
              :submit-on-enter="false"
            />
            <span v-if="fieldErrors.command" class="uc-error">{{ fieldErrors.command }}</span>
          </label>

          <label class="uc-check">
            <input v-model="form.use_shell" type="checkbox" />
            <span>
              Run it through a shell
              <span class="uc-hint-inline">
                — needed for pipes, <code>&amp;&amp;</code> and redirects. Without it a
                semicolon in a filename stays part of the filename.
              </span>
            </span>
          </label>

          <div class="uc-says">
            <label class="uc-field">
              <span class="uc-label">SAYS WHEN STARTING</span>
              <input v-model="form.say_starting" type="text" class="uc-input"
                     placeholder="Backing up your notes." autocomplete="off" />
              <span class="uc-hint">As it launches.</span>
            </label>
            <label class="uc-field">
              <span class="uc-label">SAYS WHILE IT RUNS</span>
              <input v-model="form.say_running" type="text" class="uc-input"
                     placeholder="This takes a moment." autocomplete="off" />
              <span class="uc-hint">With it, while it's still going.</span>
            </label>
            <label class="uc-field">
              <span class="uc-label">SAYS WHEN FINISHED</span>
              <input v-model="form.say_finished" type="text" class="uc-input"
                     placeholder="Your notes are backed up." autocomplete="off" />
              <span class="uc-hint">After it exits, above its output.</span>
            </label>
          </div>

          <label class="uc-field uc-field-short">
            <span class="uc-label">GIVE UP AFTER (SECONDS)</span>
            <input v-model.number="form.timeout_seconds" type="number"
                   class="uc-input" min="1" max="3600" />
          </label>

          <div class="uc-form-actions btn-row end">
            <button type="button" class="btn-ghost" @click="cancelForm">Cancel</button>
            <button type="submit" class="btn-primary" :disabled="saving">
              {{ saving ? 'Saving…' : (editing ? 'Save changes' : 'Create') }}
            </button>
          </div>
        </form>

        <p v-if="loading" class="uc-empty">Loading…</p>

        <p v-else-if="!commands.length" class="uc-empty">
          You haven't written any yet. <strong>New command</strong> starts one.
        </p>

        <ul v-else class="uc-list">
          <li
            v-for="(command, index) in commands"
            :key="command.slug"
            class="uc-row"
            :class="{ chosen: selected.has(command.slug), active: running?.slug === command.slug }"
          >
            <label class="uc-row-select">
              <input
                type="checkbox"
                :checked="selected.has(command.slug)"
                :aria-label="`Select ${command.name}`"
                @change="toggleSelected(command.slug)"
              />
            </label>
            <div class="uc-row-main">
              <p class="uc-row-name">
                <span class="uc-row-number">{{ index + 1 }}.</span>
                {{ command.name }}
              </p>
              <p v-if="command.description" class="uc-row-desc">
                {{ command.description }}
              </p>
              <p class="uc-row-command">{{ command.command }}</p>
              <p v-if="command.say_finished" class="uc-row-says">
                “{{ command.say_finished }}”
              </p>
            </div>
            <div class="uc-row-actions btn-row compact">
              <button
                type="button"
                class="btn-ghost"
                :disabled="!!running || editing !== null"
                @click="runCommand(command)"
              >
                Run
              </button>
              <button
                type="button"
                class="btn-ghost"
                :disabled="editing !== null"
                @click="startModifying(command)"
              >
                Modify
              </button>
            </div>
          </li>
        </ul>

        <p v-if="error" class="uc-error uc-error-block">{{ error }}</p>
        <p v-else-if="notice" class="uc-notice">{{ notice }}</p>

        <div v-if="finished" class="uc-finished" :class="finished.status">
          <p class="uc-finished-head">{{ finished.name }}</p>
          <p class="uc-finished-text">{{ finished.text }}</p>
        </div>

        <p v-if="directory" class="uc-where">
          Stored as one file each in <code>{{ directory }}</code> — yours to read,
          copy and back up.
        </p>
      </HudPanel>
    </main>
  </div>
</template>

<style scoped>
.user-commands-panel {
  --panel-pad-x: 42px;
  width: 100%;
  max-width: 760px;
  padding: 30px 42px 36px;
}

.uc-intro {
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-muted);
  margin: 10px 0 0;
}

/* ---- what's running ---- */
.uc-running {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 18px;
  padding: 10px 14px;
  border: 1px solid var(--cyan);
  border-radius: 6px;
  background: rgba(45, 226, 230, 0.07);
}

.uc-running-dot {
  width: 8px;
  height: 8px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--cyan-bright);
  animation: ucPulse 1.1s ease-in-out infinite;
}

@keyframes ucPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}

.uc-running-text { flex: 1; min-width: 0; font-size: 13px; color: var(--text); }
.uc-running-says { display: block; margin-top: 2px; font-size: 12px; font-style: italic; color: var(--text-muted); }

/* ---- toolbar ----
   Sizing and alignment come from .btn-row in hud.css, so Select all
   sits level with New command and is the same height as it. This sets
   where the row goes, not how big the buttons in it are. */
.uc-toolbar { margin-top: 20px; }

/* ---- the form ---- */
.uc-form {
  margin-top: 20px;
  padding: 20px 20px 22px;
  border: 1px solid var(--cyan-dim);
  border-radius: 8px;
  background: rgba(45, 226, 230, 0.035);
}

.uc-form-title {
  margin: 0 0 16px;
  font-family: var(--font-display, var(--font-mono));
  font-size: 14px;
  letter-spacing: 2px;
  color: var(--cyan-bright);
}

.uc-field { display: block; margin-bottom: 15px; }
.uc-field-short { max-width: 220px; }

.uc-label {
  display: block;
  font-size: 9.5px;
  letter-spacing: 1.6px;
  color: var(--cyan-dim);
  margin-bottom: 6px;
}

/* Matched to the message box and the other inputs rather than restated,
   so a new field here looks like every field elsewhere. */
.uc-input {
  display: block;
  width: 100%;
  box-sizing: border-box;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 10px 12px;
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 13px;
  outline: none;
  transition: 0.15s ease;
}

.uc-input:focus {
  border-color: var(--cyan);
  box-shadow: 0 0 12px rgba(45, 226, 230, 0.25);
}

.uc-input::placeholder { color: var(--text-muted); opacity: 0.75; }
.uc-mono { font-size: 12.5px; }

.uc-check {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  margin-bottom: 15px;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text);
  cursor: pointer;
}

.uc-check input { accent-color: var(--cyan); margin-top: 2px; cursor: pointer; }

/* Under a field rather than inside the label, because what the field is
   and when it happens are two different sentences. */
.uc-hint {
  display: block;
  margin-top: 5px;
  font-size: 11px;
  line-height: 1.45;
  color: var(--text-muted);
}
.uc-hint-inline { color: var(--text-muted); }
.uc-check code { color: var(--cyan-dim); }

/* Three lines side by side where there is room, stacked where there is
   not - they are one thought and read better together. */
.uc-says {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px;
}

.uc-form-actions { margin-top: 18px; }

/* ---- the list ---- */
.uc-list { list-style: none; margin: 22px 0 0; padding: 0; }

.uc-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 13px 14px;
  border: 1px solid var(--border);
  border-radius: 6px;
  margin-bottom: 9px;
  background: rgba(255, 255, 255, 0.015);
  transition: 0.15s ease;
}

.uc-row:hover { border-color: var(--cyan-dim); }
.uc-row.chosen { border-color: var(--cyan); background: rgba(45, 226, 230, 0.06); }
.uc-row.active { border-color: var(--cyan-bright); }

.uc-row-select { flex: 0 0 auto; padding-top: 2px; }
.uc-row-select input { accent-color: var(--cyan); cursor: pointer; }

.uc-row-main { flex: 1; min-width: 0; }

.uc-row-name {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--cyan-bright);
  word-break: break-word;
}

.uc-row-number {
  display: inline-block;
  min-width: 1.6em;
  color: var(--cyan-dim);
  font-family: var(--font-mono);
  font-weight: 400;
}

.uc-row-desc {
  margin: 4px 0 0;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text);
  word-break: break-word;
}

.uc-row-command {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-muted);
  word-break: break-all;
}

.uc-row-says {
  margin: 5px 0 0;
  font-size: 12px;
  color: var(--text-muted);
  font-style: italic;
}

.uc-row-actions { flex: 0 0 auto; }

/* What a command said when it ended, and what it printed. Shown here
   rather than in a notice line because the output can be twenty lines
   of a build log and a one-line notice would hide nineteen of them. */
.uc-finished {
  margin-top: 16px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-left-width: 3px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.02);
}

.uc-finished.success { border-left-color: var(--cyan); }
.uc-finished.warning { border-left-color: #ffcc66; }
.uc-finished.error { border-left-color: var(--danger, #ff5470); }

.uc-finished-head {
  margin: 0 0 6px;
  font-family: var(--font-display, var(--font-mono));
  font-size: 10px;
  letter-spacing: 1.8px;
  text-transform: uppercase;
  color: var(--cyan-dim);
}

.uc-finished-text {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 260px;
  overflow: auto;
}

.uc-empty {
  margin: 22px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-muted);
}

.uc-empty strong { color: var(--cyan-dim); }

.uc-error { display: block; margin-top: 6px; font-size: 11.5px; color: #ff8098; }
.uc-error-block { margin-top: 18px; font-size: 12.5px; line-height: 1.6; }

.uc-notice {
  margin: 18px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--cyan-dim);
  white-space: pre-wrap;
  word-break: break-word;
}

.uc-where {
  margin: 22px 0 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-muted);
}

.uc-where code { color: var(--cyan-dim); word-break: break-all; }

/* The window's floor is 760px wide, so this is the tight end: the row
   actions go under the text rather than squeezing it to nothing. */
@media (max-width: 920px) {
  .user-commands-panel { --panel-pad-x: 18px; padding: 24px 18px 28px; }
  .uc-row { flex-wrap: wrap; }
  .uc-row-actions { width: 100%; justify-content: flex-end; }
  .uc-form { padding: 16px 15px 18px; }
}
</style>
