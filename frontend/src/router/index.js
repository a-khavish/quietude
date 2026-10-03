/*
 * Quietude - a personal assistant that runs on your own machine.
 * Copyright (C) 2026 Khavish Auckaloo
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
/*
 * router/index.js
 * The screens, as routes.
 *
 * In the Windows build these were a mix of three server-rendered pages
 * and seven div.screen blocks toggled by a showScreen() helper that
 * added and removed a .hidden class. Making them routes buys three
 * things: the back button works, /commands and /tts-settings have real
 * URLs you can reload, and the guard below replaces the imperative
 * routing that init() did by hand.
 *
 * The guard is the important part. A returning user must not be able to
 * reach the console by typing its URL, and someone mid-setup must not
 * land on the login screen - so entry is decided from backend state on
 * every navigation, not from whatever the last screen happened to be.
 */

import { createRouter, createWebHistory } from 'vue-router'
import { useSessionStore } from '@/stores/session'

const routes = [
  {
    path: '/',
    name: 'boot',
    // A splash that decides where to go, so no real screen owns that job.
    component: () => import('@/views/BootRouteView.vue'),
  },
  {
    path: '/agreement',
    name: 'agreement',
    component: () => import('@/views/AgreementView.vue'),
    meta: { stage: 'onboarding' },
  },
  {
    path: '/setup',
    name: 'setup',
    component: () => import('@/views/SetupView.vue'),
    meta: { stage: 'onboarding' },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginChoiceView.vue'),
    meta: { stage: 'login' },
  },
  {
    path: '/login/password',
    name: 'password-login',
    component: () => import('@/views/PasswordLoginView.vue'),
    meta: { stage: 'login' },
  },
  {
    path: '/login/face',
    name: 'face-unlock',
    component: () => import('@/views/FaceUnlockView.vue'),
    // Not stage:'login' - it doubles as the reset confirmation gate,
    // which is reached from inside an authenticated session.
    meta: { stage: 'any' },
  },
  {
    path: '/face/register',
    name: 'face-register',
    component: () => import('@/views/FaceRegisterView.vue'),
    meta: { stage: 'any' },
  },
  {
    path: '/console',
    name: 'console',
    component: () => import('@/views/ConsoleView.vue'),
    meta: { stage: 'authenticated', gateSpeech: true },
  },
  {
    path: '/commands',
    name: 'commands',
    component: () => import('@/views/CommandsView.vue'),
    meta: { stage: 'authenticated' },
  },
  {
    path: '/tts-settings',
    name: 'tts-settings',
    component: () => import('@/views/TtsSettingsView.vue'),
    meta: { stage: 'authenticated' },
  },
  {
    path: '/identity',
    name: 'identity',
    component: () => import('@/views/IdentityView.vue'),
    meta: { stage: 'authenticated' },
  },
  {
    path: '/models',
    name: 'models',
    component: () => import('@/views/ModelsView.vue'),
    meta: { stage: 'authenticated' },
  },
  {
    path: '/user-commands',
    name: 'user-commands',
    component: () => import('@/views/UserCommandsView.vue'),
    meta: { stage: 'authenticated' },
  },
  {
    path: '/app-settings',
    name: 'app-settings',
    component: () => import('@/views/AppSettingsView.vue'),
    meta: { stage: 'authenticated' },
  },
  {
    path: '/voices',
    name: 'voices',
    component: () => import('@/views/VoicesView.vue'),
    meta: { stage: 'authenticated' },
  },
  {
    path: '/reset',
    name: 'reset',
    component: () => import('@/views/ResetView.vue'),
    meta: { stage: 'any' },
  },
  {
    path: '/shutdown',
    name: 'shutdown',
    component: () => import('@/views/ShutdownView.vue'),
    meta: { stage: 'any' },
  },
  // Anything unrecognised goes back through the boot decision rather
  // than to a 404 - there is nowhere else for it to usefully go.
  { path: '/:pathMatch(.*)*', redirect: { name: 'boot' } },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

/** Where a user in this state belongs. */
export function entryRoute(status) {
  if (!status.has_users) {
    return status.agreement_accepted ? { name: 'setup' } : { name: 'agreement' }
  }
  return { name: 'login' }
}

router.beforeEach(async (to) => {
  const session = useSessionStore()
  const stage = to.meta.stage

  // The boot splash and the terminal screens decide for themselves.
  if (!stage || stage === 'any' || to.name === 'boot') return true

  // Status is fetched once and then trusted for the session; the pages
  // that change it (setup finishing, a reset) update the store directly.
  if (!session.hasUsers && !session.authenticated) {
    try {
      await session.refreshStatus()
    } catch {
      return true // offline - let the view show its own error
    }
  }

  if (stage === 'authenticated' && !session.authenticated) {
    return entryRoute({
      has_users: session.hasUsers,
      agreement_accepted: session.agreementAccepted,
    })
  }

  // Already signed in: don't go back through onboarding or login.
  // Keyed on `authenticated` rather than `user` - see the comment on
  // that field in stores/session.js for why the distinction matters.
  if (session.authenticated && (stage === 'onboarding' || stage === 'login')) {
    return { name: 'console' }
  }

  // Setup isn't finished, so login has nothing to log into.
  if (stage === 'login' && !session.hasUsers) {
    return entryRoute({
      has_users: false,
      agreement_accepted: session.agreementAccepted,
    })
  }

  // The agreement is a one-time acknowledgement that everything stays on
  // this machine, and it comes before anything else. Without this rule
  // it can be skipped by typing /setup, since both are 'onboarding' and
  // nothing else would turn that away.
  if (stage === 'onboarding' && !session.agreementAccepted && to.name !== 'agreement') {
    return { name: 'agreement' }
  }

  return true
})
