<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  HudPanel.vue
  The framed panel with bracket corners that every screen sits in.

  In the reference each screen repeated four <div class="corner"> by
  hand - 28 copies across three templates. One component instead, which
  is the smallest possible example of what the Vue rebuild is for.
-->
<script setup>
defineProps({
  /** Extra classes for the panel itself, e.g. 'setup-panel'. */
  variant: { type: String, default: '' },
  /** Panels that are pure chrome (the side panel) skip the gradient. */
  bare: { type: Boolean, default: false },
})
</script>

<template>
  <div class="hud-frame" :class="[variant, { panel: !bare }]">
    <div class="corner tl" />
    <div class="corner tr" />
    <div class="corner bl" />
    <div class="corner br" />
    <slot />
  </div>
</template>
