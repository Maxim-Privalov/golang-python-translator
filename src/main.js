import { pyInvoke } from "tauri-plugin-pytauri-api";
import mainHtml from "./main.html?raw";
import { CodeEditor, Terminal } from "./script.js"

document.getElementById("app").innerHTML = mainHtml;

const goEditor = new CodeEditor(document.getElementById('editor-go'));
const pyEditor = new CodeEditor(document.getElementById('editor-py'));

const term = new Terminal({
  box: document.getElementById('terminal'),
  out: document.getElementById('term-out'),
  input: document.getElementById('term-input'),
  prompt: document.getElementById('term-prompt'),
});

// Очистить
document.getElementById('btn-clear').addEventListener('click', () => {
  goEditor.value = '';
  pyEditor.value = '';
  goEditor.focus();
});

// Запустить
document.getElementById('btn-run').addEventListener('click', async () => {

  const code = goEditor.value;
  if (!code.trim()) {
    term.print('Ошибка: окно GO пустое. Вставьте код и повторите.', 'err');
    term.print('');
    goEditor.focus();
    return;
  }

  term.print(`Строк в GO: ${goEditor.count}`);
  let result = await pyInvoke("translate", { code: code });

  if (result.error) {
    term.print('Возникли неожиданные ошибки.', 'ok');
    term.print(result.error, 'err');
  } else {
    pyEditor.value = result.code;
    term.print('Готово. Результат в окне Python.', 'ok');
    term.print('');
  }
});

window.onload = async function() {
  const start_pth = await pyInvoke("execute_command", { command: "pwd" });
  term.changePwd(start_pth.stdout);
}

term.execute = async function(cmdline) {
  const responce = await pyInvoke("execute_command", { command: cmdline });
  term.changePwd(responce.cwd)
  term.print(responce.stdout + responce.stderr);
}