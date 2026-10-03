<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  SidePanel.vue
  User, session date, and the commands worth reaching for.

  The command list used to be eight strings hardcoded here, which had
  two problems. It was a second copy of something the backend already
  owns - so a command could be renamed, or added, and this panel would
  go on suggesting the old one - and the entries were plain text, so the
  only thing you could do with a suggestion was read it and type it out
  again.

  Now it comes from the one command reference in assistant.py, which
  marks the entries worth surfacing, and the suggestions are buttons.
  Clicking one runs it.
-->
<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import HudPanel from '@/components/HudPanel.vue'

defineProps({
  userName: { type: String, default: '—' },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['run'])

/*
 * The list if the request fails.
 *
 * Not a convenience: the console is reachable, the backend answered the
 * login, and a panel that renders empty because one call failed looks
 * like a broken screen. These are the commands that have existed for as
 * long as Quietude has, so a stale fallback is a safe one.
 */
const FALLBACK = [
  'help', 'show commands', 'show features', 'tts settings',
  'voice library', 'register face', 'clear terminal', 'shutdown',
]

const commands = ref(FALLBACK)

const sessionDate = computed(() =>
  new Date().toLocaleDateString(undefined, {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  }),
)

onMounted(async () => {
  try {
    const all = (await api.commands()).commands || []
    // `short` exists for the handful whose names are a pair of
    // alternatives ("turn on voice chat / turn off voice chat") or carry
    // a placeholder - too long for a 240px column, and only one half of
    // them is a thing you can click.
    const primary = all.filter((c) => c.primary).map((c) => c.short || c.usage || c.name)
    if (primary.length) commands.value = primary
  } catch {
    // Keep the fallback. Nothing here is worth an error message.
  }
})
</script>

<template>
  <HudPanel bare variant="side-panel">
    <div class="side-orb-wrap">
      <div class="side-orb">
        <div class="ring r1" />
        <div class="ring r2" />
        <div class="boot-core small" />
      </div>
    </div>

    <div class="side-info">
      <p class="side-label">USER</p>
      <p class="side-value">{{ userName }}</p>
      <p class="side-label">SESSION</p>
      <p class="side-value small">{{ sessionDate }}</p>
    </div>

    <div class="side-help">
      <p class="side-label">COMMANDS</p>
      <ul class="side-commands">
        <li v-for="c in commands" :key="c">
          <button
            type="button"
            class="side-command"
            :disabled="disabled"
            :title="`Run: ${c}`"
            @click="emit('run', c)"
          >
            "{{ c }}"
          </button>
        </li>
      </ul>
    </div>
  </HudPanel>
</template>
