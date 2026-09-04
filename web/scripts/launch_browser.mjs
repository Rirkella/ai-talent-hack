/**
 * Запуск браузера для съёмки скриншотов и печати PDF.
 *
 * `chromium.launch()` по умолчанию берёт «headless shell» — отдельную
 * сборку, которой может не оказаться: playwright скачивает её отдельно от
 * пакета, и после обновления пакета путь меняется, а файла нет. Тогда
 * съёмка падает с советом «npx playwright install», то есть требует
 * интернета — ровно того, чего у этого проекта на демонстрации нет.
 *
 * Здесь при отказе берётся полная сборка chromium из кеша playwright, а
 * если нет и её — системный Chrome или Edge. Все три пути локальные:
 * интернет не нужен ни в одном случае.
 *
 * Пути записаны через прямой слэш намеренно. Node принимает его на Windows,
 * а обратный слэш в исходнике — источник ошибок: при генерации файла через
 * heredoc `\P` и `\G` беззвучно превращаются в `P` и `G`, и путь
 * «C:\Program Files\Google\…» становится «C:Program FilesGoogle…».
 */

import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "playwright";

/** Полная сборка chromium из кеша playwright, если она есть. */
function cachedChromium() {
  const root = join(process.env.LOCALAPPDATA ?? "", "ms-playwright");
  if (!existsSync(root)) return null;
  for (const dir of readdirSync(root)) {
    if (!dir.startsWith("chromium-")) continue;
    const exe = join(root, dir, "chrome-win64", "chrome.exe");
    if (existsSync(exe)) return exe;
  }
  return null;
}

const SYSTEM_BROWSERS = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
];

export async function launchBrowser(options = {}) {
  try {
    return await chromium.launch(options);
  } catch (err) {
    const exe = cachedChromium() ?? SYSTEM_BROWSERS.find((p) => existsSync(p));
    if (!exe) {
      console.error(
        "Не найден ни headless shell playwright, ни chromium в его кеше, " +
          "ни системный Chrome или Edge. Установите браузер playwright " +
          "командой «npx playwright install chromium» — это единственный " +
          "шаг, которому нужен интернет.",
      );
      throw err;
    }
    console.log(`headless shell playwright не найден, беру ${exe}`);
    return await chromium.launch({ ...options, executablePath: exe });
  }
}
