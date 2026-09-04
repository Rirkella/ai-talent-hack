/**
 * HTML → PDF через Chromium, которым уже управляет Playwright.
 *
 * Почему так, а не reportlab: документ содержит таблицы, кириллицу,
 * скриншоты и вёрстку в несколько колонок. Собирать это программным
 * рисованием на канве — долго и хрупко, а Chromium уже установлен для
 * съёмки скриншотов и печатает ровно то, что видно в браузере.
 *
 * Сеть не нужна: страница локальная, шрифты системные.
 *
 *   node scripts/make_pdf.mjs <вход.html> <выход.pdf> ["Колонтитул"]
 */

import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { launchBrowser } from "./launch_browser.mjs";

const [input, output, runningTitle = ""] = process.argv.slice(2);
if (!input || !output) {
  console.error("нужны аргументы: <вход.html> <выход.pdf> [колонтитул]");
  process.exit(2);
}

const browser = await launchBrowser();
const page = await browser.newPage();

// file:// обязателен: иначе относительные ссылки на скриншоты не разрешаются.
await page.goto(pathToFileURL(resolve(input)).href, { waitUntil: "networkidle" });
// Шрифты и картинки могли ещё не разложиться по строкам.
await page.waitForTimeout(1200);

await page.pdf({
  path: resolve(output),
  format: "A4",
  printBackground: true,
  displayHeaderFooter: true,
  margin: { top: "16mm", bottom: "16mm", left: "14mm", right: "14mm" },
  headerTemplate: `
    <div style="width:100%;padding:0 14mm;font:8pt -apple-system,'Segoe UI',Arial;
                color:#8b95a1;display:flex;justify-content:space-between;">
      <span>${runningTitle}</span><span>Avito AI Reviewer</span>
    </div>`,
  footerTemplate: `
    <div style="width:100%;padding:0 14mm;font:8pt -apple-system,'Segoe UI',Arial;
                color:#8b95a1;text-align:center;">
      <span class="pageNumber"></span> / <span class="totalPages"></span>
    </div>`,
});

await browser.close();
console.log(`  ${output}`);
