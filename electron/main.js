// Quietude - a personal assistant that runs on your own machine.
// Copyright (C) 2026 Khavish Auckaloo
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The window.
//
// This is a shell and nothing more. The interface is a Vue application
// served by the Python backend over loopback, and everything the
// assistant does happens there - this process opens a window, points it
// at that address, and reports back when it is up and when it is
// closed. Swapping it for the GTK shell changes nothing a user can see.
//
// It speaks the same contract as backend/quietude/shell/window.py:
//
//   in    QUIETUDE_URL and QUIETUDE_OPTIONS in the environment
//   out   "READY" on stdout once the window is up
//         "CLOSED" on stdout when the user closed it
//         SIGTERM means quit
//
// The environment rather than argv, because Electron inserts the app
// path into argv when running unpackaged and the position of everything
// after it depends on how it was launched.
//
// Why Electron at all, when WebKitGTK is already on the machine and
// costs nothing to ship: WebKit requires a user gesture before any
// media may play, and that rule applies to a <video> fed from
// getUserMedia. The camera preview was rejected by it, which looks
// exactly like a broken camera - a still frame with the engine's own
// play button drawn over it. That specific case has a setting, but it
// was the second WebKit-only difference to cost a release, after the
// autoplay rule silenced the wake tones. Chromium is the engine the
// interface is developed and tested against, so this makes the thing
// people run the same as the thing people test.

const { app, BrowserWindow, Menu, Tray, nativeImage, screen, shell } = require('electron')
const path = require('path')

// What the window opens at, and the smallest it may be dragged to.
// These match the GTK shell exactly - the interface reflows the whole
// way down to the floor and both shells have to agree on where it is.
const PREFERRED_WIDTH = 1180
const PREFERRED_HEIGHT = 760
const FLOOR_WIDTH = 760
const FLOOR_HEIGHT = 520

// Room for the desktop's own furniture - panels, docks, title bars.
const SCREEN_MARGIN_W = 80
const SCREEN_MARGIN_H = 120

function options() {
  try {
    return JSON.parse(process.env.QUIETUDE_OPTIONS || '{}')
  } catch {
    return {}
  }
}

const OPTIONS = options()
const TARGET = process.env.QUIETUDE_URL || ''
const TOKEN = process.env.QUIETUDE_TOKEN || ''

if (!TARGET) {
  console.error('[shell] no address to open')
  app.exit(2)
}

// The address the window is allowed to be at. Anything else - a link in
// a reply, a redirect, a page that tries to navigate itself somewhere -
// opens in the user's real browser instead, so that nothing can quietly
// replace the interface with something that looks like it.
let origin = null
try {
  origin = new URL(TARGET).origin
} catch {
  origin = null
}

// One window. A second launch raises the one that exists rather than
// opening another onto the same backend.
if (!app.requestSingleInstanceLock()) {
  app.exit(0)
}

// The interface is a local page with no third-party anything in it, and
// the engine's own cache only makes a cold start slower to reason
// about. Chromium's shared-memory sandbox also needs a writable place
// to live, which some container and hardened setups do not give it; the
// launcher passes --no-sandbox in exactly those cases and nowhere else.
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required')

let mainWindow = null
let quitting = false

function openingSize() {
  const area = screen.getPrimaryDisplay().workAreaSize
  return {
    width: Math.min(PREFERRED_WIDTH,
                    Math.max(FLOOR_WIDTH, area.width - SCREEN_MARGIN_W)),
    height: Math.min(PREFERRED_HEIGHT,
                     Math.max(FLOOR_HEIGHT, area.height - SCREEN_MARGIN_H)),
  }
}

function create() {
  const size = openingSize()
  const area = screen.getPrimaryDisplay().workAreaSize

  mainWindow = new BrowserWindow({
    width: size.width,
    height: size.height,
    // The floor, clamped to the screen as well: a minimum larger than
    // the display has the same effect as no minimum at all, except
    // that the bottom of the window is off the bottom of the screen.
    minWidth: Math.min(FLOOR_WIDTH, Math.max(360, area.width - SCREEN_MARGIN_W)),
    minHeight: Math.min(FLOOR_HEIGHT, Math.max(320, area.height - SCREEN_MARGIN_H)),
    center: true,
    title: OPTIONS.title || 'Quietude',
    // Matches the interface's own background, so there is no white
    // flash between the window appearing and the page painting.
    backgroundColor: '#05080d',
    autoHideMenuBar: true,
    // Shown on ready-to-show rather than immediately, for the same
    // reason: an empty window that then fills in reads as a slow start.
    show: false,
    icon: OPTIONS.icon_file || undefined,
    webPreferences: {
      // The page is served over loopback and has no business reaching
      // into this process. It talks to the backend over HTTP like any
      // other page, which is the whole interface Quietude needs.
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      spellcheck: false,
      // Stops a page from being able to open a window that outlives
      // this one's lifetime handling.
      nativeWindowOpen: false,
    },
  })

  mainWindow.setMenuBarVisibility(false)

  // The secret that makes this window the only thing the server will
  // answer. Attached here rather than anywhere in the page, because the
  // page cannot set a header on the navigation that loads it - which is
  // exactly why a browser pointed at the same address gets nowhere.
  if (TOKEN) {
    const filter = origin ? { urls: [`${origin}/*`] } : { urls: ['<all_urls>'] }
    mainWindow.webContents.session.webRequest.onBeforeSendHeaders(
      filter,
      (details, callback) => {
        callback({
          requestHeaders: { ...details.requestHeaders, 'X-Quietude-Window': TOKEN },
        })
      })
  }

  // The microphone and the camera, granted for this address and nothing
  // else, without a prompt.
  //
  // The prompt would be theatre. This is a local application the user
  // installed and launched, talking to a server on their own loopback,
  // and there is no other origin it can load - the navigation handlers
  // below see to that. Everything else a page can ask for is refused
  // rather than ignored, so a new permission arriving in some later
  // Chromium is denied by default rather than inherited.
  const ALLOWED = new Set(['media', 'audioCapture', 'videoCapture'])
  mainWindow.webContents.session.setPermissionRequestHandler(
    (contents, permission, callback) => {
      const url = contents.getURL()
      const sameOrigin = origin && url.startsWith(origin)
      callback(Boolean(sameOrigin) && ALLOWED.has(permission))
    })
  mainWindow.webContents.session.setPermissionCheckHandler(
    (contents, permission, requestingOrigin) =>
      Boolean(origin) && requestingOrigin === origin && ALLOWED.has(permission))

  // Navigation away from the interface opens in the real browser. A
  // link in a reply should still work; it just should not be able to
  // take over the window the assistant lives in.
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (origin && url.startsWith(origin)) return
    event.preventDefault()
    shell.openExternal(url).catch(() => {})
  })
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url && !url.startsWith('about:')) shell.openExternal(url).catch(() => {})
    return { action: 'deny' }
  })

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    refreshCloseAction()
    // The launcher is waiting for this: it is the difference between
    // "the window opened" and "the shell exited before it ever
    // appeared", and it cannot tell those apart from the process
    // staying alive for a moment.
    process.stdout.write('READY\n')
  })

  // What the close button does, asked at the moment it is pressed.
  //
  // Genuinely asked, not remembered. The first version of this kept a
  // copy refreshed when the window gained focus, which sounds
  // equivalent and is not: you change the setting in a window that
  // already has focus, so no focus event fires, so the copy is never
  // refreshed, so closing does the old thing. The setting saved
  // correctly and did nothing, which is the worst way for a setting to
  // fail.
  //
  // A close cannot be awaited, so it is always cancelled first and then
  // acted on once the answer arrives. The request is to a server on
  // loopback already answering this window; the delay is not visible.
  mainWindow.on('close', (event) => {
    if (quitting) return
    event.preventDefault()
    refreshCloseAction().then(() => {
      if (lastCloseAction === 'tray') {
        mainWindow.hide()
        ensureTray()
        return
      }
      quitting = true
      process.stdout.write('CLOSED\n')
      app.quit()
    })
  })

  // Re-asked on focus, which is the cheapest moment that reliably
  // follows someone having changed the setting.
  mainWindow.on('focus', refreshCloseAction)

  mainWindow.on('closed', () => { mainWindow = null })

  mainWindow.webContents.on('did-fail-load', (_e, code, description, url) => {
    // -3 is ERR_ABORTED, which a cancelled navigation reports and which
    // is not a failure worth saying anything about.
    if (code === -3) return
    console.error(`[shell] could not load ${url}: ${description}`)
  })

  // The cookie that covers what the header cannot.
  //
  // The header above is attached to requests the window makes. An
  // AudioWorklet module is fetched by the audio engine rather than by
  // the page, and carries none of the page's headers - so the wake word
  // could not start while everything visible worked. A cookie is
  // attached by the engine to all of it.
  //
  // httpOnly so the page cannot read it back out, SameSite=Strict so
  // nothing else can cause it to be sent, and no expiry so it lives
  // only as long as this session - the secret is minted per launch
  // anyway.
  const ready = TOKEN && origin
    ? mainWindow.webContents.session.cookies.set({
        url: origin,
        name: 'quietude_window',
        value: TOKEN,
        httpOnly: true,
        sameSite: 'strict',
      }).catch((err) => {
        console.error(`[shell] could not set the window cookie: ${err}`)
      })
    : Promise.resolve()

  ready.then(() => mainWindow.loadURL(TARGET))
}

app.on('second-instance', () => {
  if (!mainWindow) return
  // Also un-hides: with the tray setting on, a second launch is how
  // most people expect to get a hidden window back.
  if (!mainWindow.isVisible()) mainWindow.show()
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.focus()
})

// The last answer from the server about what closing should do. Kept
// only so the close handler, which cannot wait for a request, has
// something to act on; refreshed whenever the window gains focus and
// right after it opens, which covers changing the setting and then
// closing the window.
let lastCloseAction = 'quit'

async function refreshCloseAction() {
  if (!origin || !TOKEN) return
  try {
    const response = await fetch(`${origin}/api/window/close-action`, {
      headers: { 'X-Quietude-Window': TOKEN },
    })
    if (!response.ok) return
    const data = await response.json()
    if (data && typeof data.close_action === 'string') {
      lastCloseAction = data.close_action
    }
  } catch {
    // Unreachable means the backend is going away, and a window that
    // hides itself while the backend shuts down would be a window
    // nobody can get back. Leave the last answer alone.
  }
}

let tray = null

function ensureTray() {
  if (tray) return tray
  const iconPath = OPTIONS.icon_file
  const image = iconPath ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty()
  try {
    tray = new Tray(image.isEmpty() ? nativeImage.createEmpty() : image)
  } catch {
    // Some desktops have no tray at all. Rather than leave the window
    // hidden with no way back, bring it straight back and let the user
    // see that closing did not minimise.
    if (mainWindow) mainWindow.show()
    return null
  }
  tray.setToolTip('Quietude')
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open Quietude', click: () => { if (mainWindow) { mainWindow.show(); mainWindow.focus() } } },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        quitting = true
        process.stdout.write('CLOSED\n')
        app.quit()
      },
    },
  ]))
  tray.on('click', () => {
    if (!mainWindow) return
    if (mainWindow.isVisible()) mainWindow.hide()
    else { mainWindow.show(); mainWindow.focus() }
  })
  return tray
}

app.on('window-all-closed', () => {
  // Closing the window is quitting. Said out loud rather than inferred,
  // so the launcher can tell this apart from a shell that died. The
  // close handler above usually says it first, which is why this only
  // speaks when nobody has.
  if (!quitting) {
    quitting = true
    process.stdout.write('CLOSED\n')
  }
  app.quit()
})

for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => {
    // The launcher is closing us, so this is not a user quit and must
    // not be reported as one - that would be Quietude shutting itself
    // down in response to its own shutdown.
    quitting = true
    app.quit()
  })
}

app.whenReady().then(create)
