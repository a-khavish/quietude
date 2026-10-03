<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!-- The bracketed camera viewport with its sweeping scan line. Flashes
     green via the `success` class when a face is registered or
     recognized. Exposes the <video> element so the owning view can hand
     it to useCamera and grab frames from it. -->
<script setup>
import { ref } from 'vue'

defineProps({
  success: { type: Boolean, default: false },
})

const video = ref(null)
defineExpose({ video })
</script>

<template>
  <div class="camera-frame" :class="{ success }">
    <video ref="video" autoplay playsinline muted />
    <div class="scan-overlay"><div class="scan-line" /></div>
    <div class="camera-brackets">
      <span /><span /><span /><span />
    </div>
  </div>
</template>
