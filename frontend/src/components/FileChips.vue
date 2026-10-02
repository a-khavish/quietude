<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!-- Attached files, as fixed-size chips that scroll horizontally when
     several are attached at once. -->
<script setup>
defineProps({
  files: { type: Array, required: true },
})

const ICONS = {
  image: '\u{1F5BC}\u{FE0F}', pdf: '\u{1F4D5}', doc: '\u{1F4C4}',
  sheet: '\u{1F4CA}', slides: '\u{1F4D1}', audio: '\u{1F3B5}',
  video: '\u{1F3AC}', archive: '\u{1F5DC}\u{FE0F}', text: '\u{1F4C3}',
  other: '\u{1F4C1}',
}

const BY_EXT = {
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image',
  svg: 'image', bmp: 'image', avif: 'image',
  pdf: 'pdf', doc: 'doc', docx: 'doc',
  xls: 'sheet', xlsx: 'sheet', csv: 'sheet',
  ppt: 'slides', pptx: 'slides',
  mp3: 'audio', wav: 'audio', ogg: 'audio', flac: 'audio', m4a: 'audio',
  mp4: 'video', mov: 'video', avi: 'video', mkv: 'video', webm: 'video',
  zip: 'archive', rar: 'archive', '7z': 'archive', tar: 'archive', gz: 'archive',
  txt: 'text', md: 'text', log: 'text', json: 'text', xml: 'text',
}

const iconFor = (name) =>
  ICONS[BY_EXT[(name.split('.').pop() || '').toLowerCase()] || 'other']

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
</script>

<template>
  <div class="bubble user file-message">
    <div class="file-chips-row">
      <div v-for="file in files" :key="file.name" class="file-chip">
        <div class="file-chip-icon">{{ iconFor(file.name) }}</div>
        <div class="file-chip-name" :title="file.name">{{ file.name }}</div>
        <div class="file-chip-size">{{ formatSize(file.size) }}</div>
      </div>
    </div>
  </div>
</template>
