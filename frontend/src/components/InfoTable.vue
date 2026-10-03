<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!--
  InfoTable.vue
  The account-information table.

  Values arrive already redacted by the backend - the password as a hash
  preview, the API key as a ciphertext preview. Nothing is masked here,
  because masking in the view would mean the real value had been sent to
  the browser in the first place.
-->
<script setup>
defineProps({
  /** [[label, value], ...] exactly as Quietude._account_table builds it. */
  rows: { type: Array, required: true },
})
</script>

<template>
  <div class="bubble quietude info-table-bubble">
    <table class="info-table">
      <tbody>
        <tr v-for="[label, value] in rows" :key="label">
          <th>{{ label }}</th>
          <td>{{ value }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
