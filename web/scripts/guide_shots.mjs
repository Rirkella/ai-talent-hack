/**
 * Кадры для руководства пользователя (`docs/guide.md`).
 *
 * Отличие от двух соседних скриптов: `screenshots.mjs` снимает страницы
 * целиком — это опись интерфейса; `story_shots.mjs` снимает блоки в порядке
 * рассказа на защите. Здесь снимаются **шаги**: что видит человек, когда
 * выполняет инструкцию по пунктам.
 *
 * Тема светлая: руководство читают с листа и печатают.
 *
 *   cd web && npm run guide-shots
 */

import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { launchBrowser } from "./launch_browser.mjs";

const BASE = process.argv[2] ?? "http://127.0.0.1:8000";
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const OUT = resolve(ROOT, "docs/screenshots/guide");

const USERS = {
  coordinator: { id: "coord-1", name: "Ирина Соколова", role: "coordinator" },
  reviewer: { id: "rev-2", name: "Ольга Дьяченко", role: "reviewer" },
  reviewerQA: { id: "rev-3", name: "Тимур Гареев", role: "reviewer" },
  student: { id: "stu-1", name: "Анна Лебедева", role: "student" },
};

mkdirSync(OUT, { recursive: true });

const browser = await launchBrowser();
const context = await browser.newContext({
  viewport: { width: 1400, height: 950 },
  deviceScaleFactor: 2,
  locale: "ru-RU",
});
const page = await context.newPage();
await page.addInitScript(() => localStorage.setItem("avito_reviewer_theme", "light"));

// Липкая шапка при съёмке отдельного блока ложится поверх кадра.
const UNSTICK = "header { position: static !important; }";

let taken = 0;

async function login(role, view) {
  await page.addInitScript(
    (u) => sessionStorage.setItem("avito_reviewer_user", JSON.stringify(u)),
    USERS[role],
  );
  await page.goto(`${BASE}/?t=${Date.now()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2200);
  await page.addStyleTag({ content: UNSTICK });
  if (view) {
    await page.getByRole("button", { name: view, exact: true }).click();
    await page.waitForTimeout(1600);
  }
}

async function open(title) {
  const head = page.locator(`.card > div > button:has-text("${title}")`).first();
  if ((await head.count()) === 0) return false;
  const label = (await head.textContent()) ?? "";
  if (label.trim().startsWith("▸")) {
    await head.click();
    await page.waitForTimeout(800);
  }
  return true;
}

/** Переключает вкладку этапа у методиста. */
async function stage(title) {
  const tab = page.getByRole("button", { name: title, exact: true });
  if ((await tab.count()) === 0) return;
  await tab.first().click();
  await page.waitForTimeout(1200);
}

async function shotSection(file, title, note) {
  if (title && !(await open(title))) {
    console.log(`  ${file}.png — пропущен: нет секции «${title}»`);
    return;
  }
  const card = page.locator(`.card:has(> div > button:has-text("${title}"))`).first();
  await card.scrollIntoViewIfNeeded();
  await page.waitForTimeout(350);
  await card.screenshot({ path: resolve(OUT, `${file}.png`) });
  taken += 1;
  console.log(`  ${file}.png  — ${note}`);
}

async function shotPage(file, note) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: resolve(OUT, `${file}.png`), fullPage: false });
  taken += 1;
  console.log(`  ${file}.png  — ${note}`);
}

async function shotLocator(file, locator, note) {
  if ((await locator.count()) === 0) {
    console.log(`  ${file}.png — пропущен: элемент не найден`);
    return;
  }
  const el = locator.first();
  await el.scrollIntoViewIfNeeded();
  await page.waitForTimeout(350);
  await el.screenshot({ path: resolve(OUT, `${file}.png`) });
  taken += 1;
  console.log(`  ${file}.png  — ${note}`);
}

// ── шаг 1: вход ───────────────────────────────────────────────────────────────
await page.addInitScript(() => sessionStorage.removeItem("avito_reviewer_user"));
await page.goto(`${BASE}/?t=${Date.now()}`, { waitUntil: "networkidle" });
await page.waitForTimeout(1400);
await shotPage("01-vhod", "форма входа");

const hint = page.getByRole("button", { name: /Демонстрационные доступы/ });
if ((await hint.count()) > 0) {
  await hint.first().click();
  await page.waitForTimeout(600);
  await shotLocator("02-dostupy", page.locator("form.card"), "логины по ролям");
}

// ── шаг 2: методист ───────────────────────────────────────────────────────────
await login("coordinator");
await shotPage("03-metodist", "экран методиста: вкладки этапов");
await shotSection("04-poryadok", "С чего начать", "порядок действий");
await shotSection("05-kriterii", "Критерии оценивания", "критерии из условия, кнопка утверждения");
await shotSection("07-sroki", "Сроки и штрафы", "три срока и режим демонстрации");

await stage("2. Работы");
await shotSection("06-zagruzka", "Загрузка работ", "загрузка пачкой");
await shotSection("08-raspredelenie", "Распределение по ревьюерам", "кто что получил и нагрузка");
await shotSection("09-raboty", "Работы", "таблица работ и выгрузка");

await stage("Настройки");
await shotSection("09b-formula", "Формула приоритета", "веса порядка ручной проверки");

// ── шаг 3: ревьюер ────────────────────────────────────────────────────────────
// Ревьюер второго курса: именно у него в очереди лежат проверенные
// работы, а значит есть что показать на всех шагах карточки.
await login("reviewerQA");
await page.addStyleTag({ content: "aside { align-self: start !important; }" });
await shotLocator("10-ochered", page.locator("aside"), "очередь по приоритету");
await shotLocator(
  "11-karta-raboty",
  page.locator(".card").first(),
  "шапка карточки: балл и кнопка запуска проверки",
);
await shotSection("12-formalnye", "Формальные проверки", "проверки без модели");
await shotSection("13-kriterii-ocenka", "Оценка по критериям", "баллы с цитатами");
await shotSection("14-priznaki-ii", "Признаки генеративного ИИ", "сигнал с основаниями");
await shotSection("15-dokument", "Документ с подсветкой", "просмотр работы со слоями");
await shotLocator(
  "16-itog",
  page.locator('.card:has-text("Итоговое решение")'),
  "подтверждение результата",
);

// ── шаг 4: студент ────────────────────────────────────────────────────────────
await login("student");
await shotSection("17-sdat", "Сдать работу", "форма сдачи, в том числе своя тема");
await shotPage("18-student", "экран студента целиком");

// ── шаг 5: качество и приватность ─────────────────────────────────────────────
await login("coordinator");
await stage("3. Аналитика");
await shotSection("18b-analitika", "Аналитика потока", "воронка и распределение баллов");

await login("coordinator", "Качество");
await shotSection("19-effekt", "Эффект: измеримые показатели", "показатели из критериев кейса");
await shotSection("20-kachestvo", "Насколько точно система оценивает работы", "результаты бенчмарка");
await login("coordinator", "Приватность");
await shotPage("21-privatnost", "офлайн-контур и ПДн");

console.log(`\nГотово: ${taken} кадров в docs/screenshots/guide/`);
await browser.close();
