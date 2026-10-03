import { app, BrowserWindow, shell, WebContentsView, ipcMain } from 'electron';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Enable Chrome DevTools Protocol so Playwright can attach to this browser.
app.commandLine.appendSwitch('remote-debugging-port', '9222');

let mainWindow;
const isVisualCursorEnabled = process.env.VISUAL_CURSOR_ENABLED !== 'false';
let cursorWindow = null;

function resetVisualCursor() {
  if (cursorWindow && !cursorWindow.isDestroyed()) {
    cursorWindow.webContents.send('cursor-reset');
  }
}
const views = new Map(); // tabId -> WebContentsView
const hiddenAgentViews = new Map(); // POC-only hidden browser-use views
let activeTabId = null;
let currentBounds = { x: 0, y: 0, width: 0, height: 0, isHidden: true };

function getNavState(webContents) {
  const history = webContents.navigationHistory;
  if (history && typeof history.canGoBack === 'function') {
    return {
      canGoBack: history.canGoBack(),
      canGoForward: history.canGoForward(),
    };
  }
  return {
    canGoBack: typeof webContents.canGoBack === 'function' ? webContents.canGoBack() : false,
    canGoForward: typeof webContents.canGoForward === 'function' ? webContents.canGoForward() : false,
  };
}

function goBack(webContents) {
  const history = webContents.navigationHistory;
  if (history && typeof history.goBack === 'function') {
    if (history.canGoBack()) history.goBack();
    return;
  }
  if (typeof webContents.canGoBack === 'function' && webContents.canGoBack()) {
    webContents.goBack();
  }
}

function goForward(webContents) {
  const history = webContents.navigationHistory;
  if (history && typeof history.goForward === 'function') {
    if (history.canGoForward()) history.goForward();
    return;
  }
  if (typeof webContents.canGoForward === 'function' && webContents.canGoForward()) {
    webContents.goForward();
  }
}

function normalizeUrl(url) {
  if (!url || url === 'about:blank') return 'comex://launchpad';
  return url;
}

function normalizeTitle(url, title) {
  if (normalizeUrl(url) === 'comex://launchpad') return 'Comex Launchpad';
  return title;
}

function notifyRenderer(tabId, data) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('tab-update', tabId, data);
}

function detachView(view) {
  if (!mainWindow || mainWindow.isDestroyed() || !view) return;
  try {
    mainWindow.contentView.removeChildView(view);
  } catch {
    // View was not attached.
  }
}

function syncActiveView() {
  if (!mainWindow || mainWindow.isDestroyed()) return;

  console.log('[SYNC] syncActiveView called. activeTabId:', activeTabId, 'isHidden:', currentBounds.isHidden);
  console.log('[SYNC] currentBounds:', JSON.stringify(currentBounds));

  // First, determine if we should show a view at all
  const shouldShowView = activeTabId && views.has(activeTabId) && !currentBounds.isHidden;

  console.log('[SYNC] shouldShowView:', shouldShowView);

  // CRITICAL FIX: Always detach ALL views first
  // This ensures no WebContentsView is covering the React UI when not needed
  for (const [tabId, view] of views.entries()) {
    try {
      detachView(view);
      if (typeof view.setVisible === 'function') {
        view.setVisible(false);
      }
      console.log('[SYNC] Detached and hid view for tabId:', tabId);
    } catch (e) {
      console.log('[SYNC] Error detaching view:', e.message);
    }
  }

  // If no view should be shown (internal URL or no active tab), we're done
  if (!shouldShowView) {
    resetVisualCursor();
    console.log('[SYNC] No view to show. All views detached. React UI fully visible.');
    return;
  }

  // Now attach and configure the active view
  const view = views.get(activeTabId);
  const url = view.webContents.getURL();
  console.log('[SYNC] Active view URL:', url);
  console.log('[SYNC] contentView children before add:', mainWindow.contentView.children?.length || 0);

  // Add the view to the content view hierarchy
  mainWindow.contentView.addChildView(view);
  
  console.log('[SYNC] contentView children after add:', mainWindow.contentView.children?.length || 0);

  // Clamp bounds to prevent child view from overflowing window and snapping to (0,0)
  const winBounds = mainWindow.getContentBounds();
  const clampedX = Math.max(0, Math.round(currentBounds.x || 0));

  // Ensure view.setBounds never sets y <= 0 unless explicitly in fullscreen mode
  const isFullScreen = typeof mainWindow.isFullScreen === 'function' && mainWindow.isFullScreen();
  const rawY = Math.round(currentBounds.y || 0);
  const minChromeTop = isFullScreen ? 0 : 70; // Header + Navbar + Bookmarks bar threshold
  const clampedY = isFullScreen ? Math.max(0, rawY) : Math.max(minChromeTop, rawY);

  const maxWidth = Math.max(0, winBounds.width - clampedX);
  const maxHeight = Math.max(0, winBounds.height - clampedY);

  const widthToUse = currentBounds.width > 0 ? currentBounds.width : maxWidth;
  const heightToUse = currentBounds.height > 0 ? currentBounds.height : maxHeight;

  const finalBounds = {
    x: clampedX,
    y: clampedY,
    width: Math.min(Math.max(0, Math.round(widthToUse)), maxWidth),
    height: Math.min(Math.max(0, Math.round(heightToUse)), maxHeight),
  };

  console.log('[SYNC] Setting clamped view bounds:', JSON.stringify(finalBounds));
  view.setBounds(finalBounds);

  // Make the view visible AFTER bounds are set
  if (typeof view.setVisible === 'function') {
    view.setVisible(true);
    console.log('[SYNC] Set view visible');
  }

  // Ensure active view receives focus
  if (view.webContents && typeof view.webContents.focus === 'function') {
    view.webContents.focus();
    console.log('[SYNC] Focused active view');
  }

  console.log('[SYNC] syncActiveView completed');
}

function attachTabListeners(tabId, view) {
  const { webContents } = view;

  webContents.on('did-start-loading', () => {
    console.log('[NAV] did-start-loading for tabId:', tabId);
    notifyRenderer(tabId, { isLoading: true });
  });

  webContents.on('did-stop-loading', () => {
    const url = normalizeUrl(webContents.getURL());
    console.log('[NAV] did-stop-loading for tabId:', tabId, 'url:', url);
    notifyRenderer(tabId, {
      isLoading: false,
      url,
      title: normalizeTitle(url, webContents.getTitle()),
      ...getNavState(webContents),
    });

    if (!activeTabId && views.has(tabId)) {
      activeTabId = tabId;
    }
    if (tabId === activeTabId && url !== 'comex://launchpad') {
      currentBounds.isHidden = false;
      syncActiveView();
    }
  });

  webContents.on('did-fail-load', (_event, _code, _desc, _url, isMainFrame) => {
    if (!isMainFrame) return;
    console.log('[NAV] did-fail-load for tabId:', tabId);
    notifyRenderer(tabId, { isLoading: false, ...getNavState(webContents) });
  });

  webContents.on('page-title-updated', (_event, title) => {
    const url = normalizeUrl(webContents.getURL());
    console.log('[NAV] page-title-updated for tabId:', tabId, 'title:', title);
    notifyRenderer(tabId, { title: normalizeTitle(url, title) });
  });

  webContents.on('page-favicon-updated', (_event, favicons) => {
    if (favicons && favicons.length > 0) {
      console.log('[NAV] page-favicon-updated for tabId:', tabId);
      notifyRenderer(tabId, { favicon: favicons[0] });
    }
  });

  webContents.on('did-navigate', (_event, url) => {
    const normalized = normalizeUrl(url);
    console.log('[NAV] did-navigate for tabId:', tabId, 'url:', normalized);
    notifyRenderer(tabId, {
      url: normalized,
      title: normalizeTitle(normalized, webContents.getTitle()),
      ...getNavState(webContents),
    });
  });

  webContents.on('did-navigate-in-page', (_event, url) => {
    const normalized = normalizeUrl(url);
    console.log('[NAV] did-navigate-in-page for tabId:', tabId, 'url:', normalized);
    notifyRenderer(tabId, {
      url: normalized,
      ...getNavState(webContents),
    });
  });

  webContents.on('media-started-playing', () => {
    console.log('[NAV] media-started-playing for tabId:', tabId);
    notifyRenderer(tabId, { isAudible: true });
  });

  webContents.on('media-paused', () => {
    console.log('[NAV] media-paused for tabId:', tabId);
    notifyRenderer(tabId, { isAudible: false });
  });

  webContents.setWindowOpenHandler(({ url }) => {
    console.log('[NAV] window-open-handler triggered for url:', url);
    if (url.startsWith('http://') || url.startsWith('https://')) {
      const newTabId = `tab-${Date.now()}`;
      console.log('[NAV] Creating new tab:', newTabId, 'for url:', url);
      notifyRenderer('__new_tab__', { newTabId, url });
    }
    return { action: 'deny' };
  });
}

function attachHiddenTabListeners(tabId, view) {
  const { webContents } = view;

  webContents.on('did-fail-load', (_event, _code, _desc, _url, isMainFrame) => {
    if (!isMainFrame) return;
    console.log('[HIDDEN] did-fail-load for tabId:', tabId);
  });

  webContents.on('did-stop-loading', () => {
    console.log('[HIDDEN] did-stop-loading for tabId:', tabId);
  });

  // No setWindowOpenHandler: hidden tabs must never trigger __new_tab__ in React.
  // No notifyRenderer calls: hidden tabs must never affect visible tab state.
}

function loadTabUrl(view, url) {
  if (!url || url === 'comex://launchpad' || url === 'nexus://launchpad' || url === 'about:blank') {
    view.webContents.loadURL('about:blank').catch(() => {});
    return;
  }
  view.webContents.loadURL(url).catch(() => {});
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 16, y: 16 },
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      allowRunningInsecureContent: false,
    },
  });

  // The React shell (localhost:5173) should never try to open a new browser window.
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;
  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'));
  }

  if (isVisualCursorEnabled) {
    createCursorWindow();
  }
}

function createCursorWindow() {
  if (!isVisualCursorEnabled || !mainWindow || mainWindow.isDestroyed()) return;
  if (cursorWindow && !cursorWindow.isDestroyed()) return;

  const winBounds = mainWindow.getContentBounds();
  const width = winBounds.width || 1400;
  const height = winBounds.height || 900;
  console.log('[CURSOR] Creating cursorWindow with initial bounds:', { x: winBounds.x, y: winBounds.y, width, height });

  cursorWindow = new BrowserWindow({
    width: width,
    height: height,
    x: winBounds.x,
    y: winBounds.y,
    frame: false,
    transparent: true,
    hasShadow: false,
    focusable: false,
    show: true,
    webPreferences: {
      preload: path.join(__dirname, 'cursor_preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      backgroundThrottling: false,
    },
  });

  cursorWindow.setIgnoreMouseEvents(true, { forward: true });
  cursorWindow.setAlwaysOnTop(true, 'screen-saver');
  cursorWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  cursorWindow.loadFile(path.join(__dirname, 'cursor_overlay.html'));
  cursorWindow.showInactive();

  cursorWindow.webContents.on('did-finish-load', () => {
    console.log('[CURSOR] Overlay HTML loaded successfully!');
  });
  cursorWindow.webContents.on('did-fail-load', (_e, code, desc) => {
    console.error('[CURSOR] Overlay HTML failed to load:', code, desc);
  });

  const syncCursorBounds = () => {
    if (!cursorWindow || cursorWindow.isDestroyed() || !mainWindow || mainWindow.isDestroyed()) return;
    cursorWindow.setBounds(mainWindow.getContentBounds());
  };

  mainWindow.on('resize', syncCursorBounds);
  mainWindow.on('move', syncCursorBounds);
  mainWindow.once('ready-to-show', syncCursorBounds);
  mainWindow.on('minimize', () => {
    resetVisualCursor();
  });
  mainWindow.on('closed', () => {
    if (cursorWindow && !cursorWindow.isDestroyed()) {
      cursorWindow.close();
      cursorWindow = null;
    }
  });
  // On focus: cursor remains hidden until the next agent click (no stale restore)
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

ipcMain.on('tabs-create', (_event, tabId, url) => {
  if (views.has(tabId)) return;

  const view = new WebContentsView({
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  view.webContents.setUserAgent(
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
  );

  views.set(tabId, view);
  attachTabListeners(tabId, view);
  loadTabUrl(view, url);
});

ipcMain.on('tabs-create-hidden-agent', (_event, tabId, url) => {
  if (hiddenAgentViews.has(tabId) || views.has(tabId)) return;

  const view = new WebContentsView({
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  view.webContents.setUserAgent(
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
  );

  hiddenAgentViews.set(tabId, view);
  attachTabListeners(tabId, view);
  
  // Append tabId as a hash so agent_service can unambiguously locate it via CDP
  const targetUrl = url ? `${url}#${tabId}` : `about:blank#${tabId}`;
  loadTabUrl(view, targetUrl);
});

ipcMain.on('tabs-destroy-hidden-agent', (_event, tabId) => {
  const view = hiddenAgentViews.get(tabId);
  if (!view) return;
  if (!view.webContents.isDestroyed()) view.webContents.close();
  hiddenAgentViews.delete(tabId);
});

ipcMain.on('tabs-destroy', (_event, tabId) => {
  const view = views.get(tabId);
  if (!view) return;

  detachView(view);
  if (!view.webContents.isDestroyed()) {
    view.webContents.close();
  }
  views.delete(tabId);

  if (activeTabId === tabId) {
    activeTabId = null;
  }
});

ipcMain.on('tabs-set-active', (_event, tabId) => {
  activeTabId = tabId;
  syncActiveView();
});

ipcMain.on('tabs-navigate', (_event, tabId, url) => {
  const view = views.get(tabId);
  if (!view) return;
  loadTabUrl(view, url);
});

ipcMain.on('tabs-go-back', (_event, tabId) => {
  const view = views.get(tabId);
  if (view) goBack(view.webContents);
});

ipcMain.on('tabs-go-forward', (_event, tabId) => {
  const view = views.get(tabId);
  if (view) goForward(view.webContents);
});

ipcMain.on('tabs-reload', (_event, tabId) => {
  const view = views.get(tabId);
  if (!view) return;
  const url = view.webContents.getURL();
  if (!url || url === 'about:blank') return;
  view.webContents.reload();
});

ipcMain.on('tabs-set-bounds', (_event, bounds) => {
  console.log('[MAIN] tabs-set-bounds received:', bounds);
  currentBounds = bounds || currentBounds;
  syncActiveView();
});

ipcMain.on('cursor-animate', (_event, data) => {
  console.log(`[CURSOR] Received cursor event x=${data?.x} y=${data?.y} tabId=${data?.tabId}`);
  if (!isVisualCursorEnabled) {
    console.log('[CURSOR] Ignored: isVisualCursorEnabled is false');
    return;
  }
  if (!cursorWindow || cursorWindow.isDestroyed()) {
    console.log('[CURSOR] cursorWindow is null/destroyed, lazily initializing...');
    createCursorWindow();
  }
  if (!cursorWindow || cursorWindow.isDestroyed()) {
    console.error('[CURSOR] Could not create cursorWindow!');
    return;
  }

  // Background-tab filtering: only animate if event is for the active foreground tab
  const isTargetActive = !data?.tabId || data.tabId === 'active_tab' || data.tabId === activeTabId;
  if (!isTargetActive) {
    console.log('[CURSOR] Dropped background tab event:', data.tabId, 'vs active:', activeTabId);
    return;
  }
  // Coordinate translation: currentBounds.x + target_x, currentBounds.y + target_y
  const viewX = Math.round(currentBounds.x || 0);
  const viewY = Math.round(currentBounds.y || 0);
  const targetX = Math.round(viewX + (data?.x || 0));
  const targetY = Math.round(viewY + (data?.y || 0));

  console.log('[CURSOR] Dispatching to cursorWindow at translated:', { x: targetX, y: targetY });
  cursorWindow.webContents.send('cursor-animate', {
    x: targetX,
    y: targetY,
    action: data?.action || 'click',
  });
});

ipcMain.on('cursor-reset', () => {
  resetVisualCursor();
});
