<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!-- A borderless icon button. The shared sizing and hover treatment for
     the copy, speaker, expand and playback controls. -->
<template>
  <button type="button" class="icon-btn">
    <slot />
  </button>
</template>

<style scoped>
.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  border-radius: 4px;
  transition: 0.15s ease;
}
.icon-btn:hover { color: var(--cyan-bright); background: rgba(45, 226, 230, 0.12); }
.icon-btn :deep(svg) { width: 13px; height: 13px; }
</style>
