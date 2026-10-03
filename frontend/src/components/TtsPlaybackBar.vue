<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  TtsPlaybackBar.vue
  Playback controls for a message being read aloud on demand.

  The reference polled /api/tts/status every 400ms to drive this, with a
  guard for the case where "idle" meant "still synthesizing" rather than
  "finished". Now the state comes straight off the <audio> element in
  useTts, so there's nothing to poll and nothing to disambiguate.
-->
<script setup>
import IconButton from '@/components/IconButton.vue'

const props = defineProps({
  preview: { type: String, default: '' },
  playing: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle', 'stop'])

const PREVIEW_MAX = 90
</script>

<template>
  <div class="tts-playback-bar">
    <svg viewBox="0 0 24 24" fill="none" class="tts-bar-icon">
      <path d="M11 5 6 9H3v6h3l5 4V5Z" stroke="currentColor"
            stroke-width="1.6" stroke-linejoin="round" />
      <path d="M15.5 9a4 4 0 0 1 0 6" stroke="currentColor"
            stroke-width="1.6" stroke-linecap="round" />
      <path d="M18 6.5a8 8 0 0 1 0 11" stroke="currentColor"
            stroke-width="1.6" stroke-linecap="round" />
    </svg>

    <span class="tts-preview-text">
      {{ preview.length > PREVIEW_MAX ? preview.slice(0, PREVIEW_MAX) + '…' : preview }}
    </span>

    <IconButton
      class="tts-control-btn"
      :title="playing ? 'Pause' : 'Play'"
      @click="emit('toggle')"
    >
      <svg v-if="playing" viewBox="0 0 24 24" fill="none">
        <path d="M8 5v14M16 5v14" stroke="currentColor"
              stroke-width="2.2" stroke-linecap="round" />
      </svg>
      <svg v-else viewBox="0 0 24 24" fill="none">
        <path d="M7 5l12 7-12 7V5Z" fill="currentColor" />
      </svg>
    </IconButton>

    <IconButton class="tts-control-btn" title="Stop" @click="emit('stop')">
      <svg viewBox="0 0 24 24" fill="none">
        <rect x="6" y="6" width="12" height="12" rx="1.5"
              stroke="currentColor" stroke-width="2.2" />
      </svg>
    </IconButton>
  </div>
</template>

<style scoped>
.tts-control-btn :deep(svg) { width: 15px; height: 15px; }
</style>
