<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!-- The full-screen boot / shutdown / transition overlay. -->
<script setup>
defineProps({
  label: { type: String, required: true },
  subtext: { type: String, default: '' },
  shuttingDown: { type: Boolean, default: false },
})
</script>

<template>
  <div class="overlay">
    <div class="boot-ring" :class="{ 'shutting-down': shuttingDown }">
      <div class="ring r1" />
      <div class="ring r2" />
      <div class="ring r3" />
      <div class="boot-core" />
    </div>
    <p class="boot-text">{{ label }}</p>
    <p v-if="subtext" class="boot-subtext">{{ subtext }}</p>
  </div>
</template>
