import { pyInvoke } from "tauri-plugin-pytauri-api";

document.getElementById("btn").addEventListener("click", async () => {
  document.querySelector("#out").textContent = await pyInvoke("translate", { code: document.querySelector("#code").value });
});