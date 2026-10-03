import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('nexusNative', {
  isElectron: true,
  platform: process.platform,
  openExternal: (url) => ipcRenderer.send('open-external', url),
  tabs: {
    create: (tabId, url) => ipcRenderer.send('tabs-create', tabId, url),
    destroy: (tabId) => ipcRenderer.send('tabs-destroy', tabId),
    setActive: (tabId) => ipcRenderer.send('tabs-set-active', tabId),
    navigate: (tabId, url) => ipcRenderer.send('tabs-navigate', tabId, url),
    goBack: (tabId) => ipcRenderer.send('tabs-go-back', tabId),
    goForward: (tabId) => ipcRenderer.send('tabs-go-forward', tabId),
    reload: (tabId) => ipcRenderer.send('tabs-reload', tabId),
    setBounds: (bounds) => ipcRenderer.send('tabs-set-bounds', bounds),
    createHiddenAgentTab: (tabId, url) => ipcRenderer.send('tabs-create-hidden-agent', tabId, url),
    destroyHiddenAgentTab: (tabId) => ipcRenderer.send('tabs-destroy-hidden-agent', tabId),
    onUpdate: (callback) => {
      ipcRenderer.removeAllListeners('tab-update');
      ipcRenderer.on('tab-update', (event, tabId, data) => callback(tabId, data));
    }
  },
  cursor: {
    animate: (data) => ipcRenderer.send('cursor-animate', data),
    reset: () => ipcRenderer.send('cursor-reset'),
  }
});
