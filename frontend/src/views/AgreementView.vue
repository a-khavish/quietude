<!--
  Quietude - a personal assistant that runs on your own machine.
  Copyright (C) 2026 Khavish Auckaloo
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<!-- The one-time local-only agreement shown on first launch. -->
<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import HudPanel from '@/components/HudPanel.vue'

const router = useRouter()
const session = useSessionStore()
const submitting = ref(false)
const error = ref(null)

const POINTS = [
  { tag: '01', text: 'Quietude runs entirely on <strong>this computer</strong>. Nothing you type or store here is sent to any company, server, or third party.' },
  { tag: '02', text: 'Any information you provide — your name, age, and account details — is saved <strong>locally</strong> in a file on your own machine, encrypted where sensitive.' },
  { tag: '03', text: 'This assistant belongs to <strong>you alone</strong>. It has no separate "owner," no remote access, and no data-sharing agreement with anyone.' },
  { tag: '04', text: 'You are responsible for keeping your own device secure, since your data lives only there.' },
]

async function accept() {
  submitting.value = true
  error.value = null
  try {
    await api.acceptAgreement()
    session.agreementAccepted = true
    router.push({ name: 'setup' })
  } catch (err) {
    error.value = err.message
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="screen">
    <HudPanel variant="agreement-panel">
      <div class="panel-header">
        <span class="eyebrow">FIRST LAUNCH · LOCAL AGREEMENT</span>
        <h1 class="wordmark">QUIETUDE</h1>
        <!-- The motto earns its place here specifically: this screen is
             where the promise is made, and the four points below are the
             fine print under it. -->
        <p class="motto">Nothing leaves this room.</p>
      </div>

      <div class="agreement-body">
        <p>Before we begin, a few things you should know:</p>
        <ul class="agreement-list">
          <!-- The markup in these points is authored here, not user
               input, so v-html is safe and keeps the inline emphasis. -->
          <li v-for="point in POINTS" :key="point.tag">
            <span class="tag">{{ point.tag }}</span><span v-html="point.text" />
          </li>
        </ul>
        <p class="fine-print">
          By continuing, you confirm you understand and accept the above.
        </p>
      </div>

      <p v-if="error" class="agreement-error">{{ error }}</p>

      <button class="btn-primary" :disabled="submitting" @click="accept">
        {{ submitting ? 'One moment…' : 'I Understand & Agree' }}
      </button>
    </HudPanel>
  </div>
</template>

<style scoped>
.motto {
  margin: 8px 0 0;
  font-size: 12.5px;
  letter-spacing: 2.6px;
  color: var(--cyan-dim);
}
.agreement-error {
  color: var(--danger);
  font-size: 12.5px;
  margin: 12px 0 0;
}
</style>
