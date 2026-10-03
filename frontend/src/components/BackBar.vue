<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  BackBar.vue
  A way back to the console that is always on screen.

  The reference pages put their back link at the very bottom of the
  panel, below the content. That was tolerable when each one was its own
  small pop-up window, but the command reference is a long table: scroll
  down it and the only way out scrolls away with you, so getting back
  means scrolling all the way up first.

  This pins it to the top of the scrolling area instead. `position:
  sticky` rather than `fixed` so it stays inside the panel's own frame
  and respects its padding, which `fixed` would ignore - it would float
  over the HUD border and sit wrong at every window size.

  The blurred, tinted backing is doing real work: the content scrolls
  underneath it, and without something opaque behind, table rows read
  straight through the button text.
-->
<script setup>
defineProps({
  label: { type: String, default: 'Back to main chat' },
})

defineEmits(['back'])
</script>

<template>
  <div class="back-bar">
    <button type="button" class="back-link back-bar-btn" @click="$emit('back')">
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"
        />
      </svg>
      {{ label }}
    </button>
  </div>
</template>

<style scoped>
.back-bar {
  position: sticky;
  top: 0;
  z-index: 4;
  display: flex;
  /* Pulled out to the panel's padding edge and back in again, so the
     backing spans the full panel width rather than leaving a strip of
     unblurred content either side of it. */
  margin: -4px calc(var(--panel-pad-x, 28px) * -1) 10px;
  padding: 10px var(--panel-pad-x, 28px);
  background: linear-gradient(
    180deg,
    rgba(8, 18, 26, 0.96) 0%,
    rgba(8, 18, 26, 0.9) 70%,
    rgba(8, 18, 26, 0) 100%
  );
  backdrop-filter: blur(6px);
}

.back-bar-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin: 0;
  width: auto;
  padding: 7px 14px 7px 10px;
}

.back-bar-btn svg {
  width: 14px;
  height: 14px;
  flex: none;
}
</style>
