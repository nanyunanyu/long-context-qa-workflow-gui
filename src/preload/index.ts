import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("lcqa", {
  apiBase: "http://127.0.0.1:8765",
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke("pick-folder"),
});
