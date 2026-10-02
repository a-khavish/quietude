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

  The list itself still comes from COMMANDS_REFERENCE in assistant.py -
  one source of truth, served as JSON rather than rendered into HTML.
-->
<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import HudPanel from '@/components/HudPanel.vue'
import BackBar from '@/components/BackBar.vue'
import { useIdentityStore } from '@/stores/identity'

const identity = useIdentityStore()

const router = useRouter()
const commands = ref([])
const error = ref(null)
const copiedName = ref(null)

onMounted(async () => {
  try {
    commands.value = (await api.commands()).commands
  } catch (err) {
    error.value = err.message
  }
})

async function copy(command) {
  try {
    await navigator.clipboard.writeText(command.usage)
    copiedName.value = command.name
    setTimeout(() => {
      if (copiedName.value === command.name) copiedName.value = null
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
              <tr v-for="command in commands" :key="command.name">
                <td class="col-name">{{ command.name }}</td>
                <td class="col-desc">{{ command.description }}</td>
                <td class="col-usage">
                  <div class="usage-cell">
                    <code class="usage-chip">{{ command.usage }}</code>
                    <button
                      type="button"
                      class="cmd-copy-btn"
                      :class="{ copied: copiedName === command.name }"
                      :title="copiedName === command.name ? 'Copied!' : 'Copy command'"
                      @click="copy(command)"
                    >
                      <svg v-if="copiedName === command.name" viewBox="0 0 24 24" fill="none">
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

      </HudPanel>
    </main>
  </div>
</template>
