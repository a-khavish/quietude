<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  CommandsView.vue
  The full command reference.

  Was a separate server-rendered page opened in its own 1040x760 browser
  window. It's a route now, which means a real URL you can reload, a
  working back button, and no second template to keep in visual sync.

  The markup deliberately mirrors templates/commands.html from the
  Windows build, class for class, so the original stylesheet's rules for
  this page apply unchanged - an earlier pass invented its own structure
  and layout here, which left that whole section of the stylesheet dead
  and the page looking nothing like the original.

  Two lists, one table. The built-ins come from COMMANDS_REFERENCE in
  assistant.py; the user's own come from their commands directory. They
  are shown together because that is how they are used - you say one or
  the other without thinking about which list it came from - and the
  filter is there for when you do care.

  The numbers run down whatever is currently showing, not down the whole
  reference. A list filtered to four commands numbered 3, 7, 11 and 24
  is a list that has learned the wrong lesson from being numbered.
-->
<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import HudPanel from '@/components/HudPanel.vue'
import BackBar from '@/components/BackBar.vue'
import { useIdentityStore } from '@/stores/identity'

const identity = useIdentityStore()

const router = useRouter()
const builtIn = ref([])
const mine = ref([])
const error = ref(null)
const copiedKey = ref(null)
const query = ref('')
const source = ref('all')   // 'all' | 'built-in' | 'mine'

onMounted(async () => {
  // Two calls, and the reference does not depend on the second. A
  // commands directory that cannot be read should cost you your own
  // commands, not the whole page.
  try {
    builtIn.value = (await api.commands()).commands.map(
      (c) => ({ ...c, source: 'built-in' }),
    )
  } catch (err) {
    error.value = err.message
  }
  try {
    mine.value = (await api.userCommands()).commands.map((c) => ({
      name: c.name,
      description: c.description || c.command,
      usage: c.name,
      slug: c.slug,
      source: 'mine',
    }))
  } catch {
    // Left empty. The reference above is the thing this page is for.
  }
})

const all = computed(() => [...builtIn.value, ...mine.value])

const counts = computed(() => ({
  all: all.value.length,
  'built-in': builtIn.value.length,
  mine: mine.value.length,
}))

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'built-in', label: 'Built in' },
  { key: 'mine', label: 'Mine' },
]

/**
 * Name, description and usage all searched, because any of the three is
 * a reasonable thing to half-remember. Someone looking for the Gemini
 * key command may remember "api", which is in the name; someone looking
 * for the one that wipes everything may remember "permanently", which
 * is only in the description.
 */
const shown = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return all.value.filter((command) => {
    if (source.value !== 'all' && command.source !== source.value) return false
    if (!needle) return true
    return [command.name, command.description, command.usage]
      .some((field) => (field || '').toLowerCase().includes(needle))
  })
})

function keyFor(command) {
  return `${command.source}:${command.name}`
}

async function copy(command) {
  try {
    await navigator.clipboard.writeText(command.usage)
    copiedKey.value = keyFor(command)
    setTimeout(() => {
      if (copiedKey.value === keyFor(command)) copiedKey.value = null
    }, 1200)
  } catch {
    // Clipboard unavailable (no permission, or an insecure context) -
    // nothing useful to say, so say nothing.
  }
}
</script>

<template>
  <div class="commands-page">
    <main class="commands-wrap">
      <HudPanel variant="commands-panel">
        <BackBar @back="router.push({ name: 'console' })" />

        <div class="panel-header commands-header">
          <span class="eyebrow">COMMAND REFERENCE</span>
          <h1 class="wordmark small">{{ identity.brand }} COMMANDS</h1>
        </div>

        <p v-if="error" class="face-status error">{{ error }}</p>

        <div class="cmd-tools">
          <label class="cmd-search">
            <span class="sr-only">Search commands</span>
            <svg class="cmd-search-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <circle cx="11" cy="11" r="6.5" stroke="currentColor" stroke-width="1.7" />
              <path
                d="M16 16l4.5 4.5" stroke="currentColor"
                stroke-width="1.7" stroke-linecap="round"
              />
            </svg>
            <input
              v-model="query"
              type="search"
              class="cmd-search-input"
              placeholder="Search commands…"
              autocomplete="off"
            />
            <button
              v-if="query"
              type="button"
              class="cmd-search-clear"
              title="Clear"
              @click="query = ''"
            >
              ×
            </button>
          </label>

          <div class="cmd-filters" role="group" aria-label="Which commands to show">
            <button
              v-for="filter in FILTERS"
              :key="filter.key"
              type="button"
              class="cmd-filter"
              :class="{ on: source === filter.key }"
              :aria-pressed="source === filter.key"
              @click="source = filter.key"
            >
              {{ filter.label }}
              <span class="cmd-filter-count">{{ counts[filter.key] }}</span>
            </button>
          </div>
        </div>

        <p class="cmd-result-line">
          <template v-if="query || source !== 'all'">
            {{ shown.length }} of {{ counts.all }}
          </template>
          <template v-else>{{ counts.all }} commands</template>
        </p>

        <div class="commands-table-wrap">
          <table class="commands-table">
            <thead>
              <tr>
                <th class="col-name">Command</th>
                <th class="col-desc">Description</th>
                <th class="col-usage">Usage</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(command, index) in shown" :key="keyFor(command)">
                <td class="col-name">
                  <span class="cmd-number">{{ index + 1 }}.</span>
                  <span class="cmd-name-text">{{ command.name }}</span>
                  <span v-if="command.source === 'mine'" class="cmd-badge">YOURS</span>
                </td>
                <td class="col-desc">{{ command.description }}</td>
                <td class="col-usage">
                  <div class="usage-cell">
                    <code class="usage-chip">{{ command.usage }}</code>
                    <button
                      type="button"
                      class="cmd-copy-btn"
                      :class="{ copied: copiedKey === keyFor(command) }"
                      :title="copiedKey === keyFor(command) ? 'Copied!' : 'Copy command'"
                      @click="copy(command)"
                    >
                      <svg v-if="copiedKey === keyFor(command)" viewBox="0 0 24 24" fill="none">
                        <path
                          d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="2"
                          stroke-linecap="round" stroke-linejoin="round"
                        />
                      </svg>
                      <svg v-else viewBox="0 0 24 24" fill="none">
                        <rect
                          x="9" y="9" width="12" height="12" rx="2"
                          stroke="currentColor" stroke-width="1.6"
                        />
                        <path
                          d="M5 15V5a2 2 0 0 1 2-2h10"
                          stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
                        />
                      </svg>
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <p v-if="!shown.length && !error" class="cmd-none">
          <template v-if="source === 'mine' && !counts.mine">
            You haven't written any of your own yet.
          </template>
          <template v-else>Nothing matches “{{ query }}”.</template>
        </p>

        <div class="cmd-footer btn-row">
          <button
            type="button"
            class="btn-ghost"
            @click="router.push({ name: 'user-commands' })"
          >
            Your own commands
          </button>
        </div>
      </HudPanel>
    </main>
  </div>
</template>
