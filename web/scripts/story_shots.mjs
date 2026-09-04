/**
 * Кадры для документа «Рассказ на защите».
 *
 * Отличие от `screenshots.mjs`: там снимаются страницы целиком — это опись
 * интерфейса. Здесь снимаются **отдельные блоки** в том порядке, в каком о
 * них говорят на защите. Страница целиком в документе нечитаема: на листе A4
 * скриншот высотой три тысячи пикселей превращается в серую полосу.
 *
 * Тема светлая: документ печатают и читают с листа.
 *
 *   cd web && npm run story-shots -- http://127.0.0.1:8010
 */

import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { launchBrowser } from "./launch_browser.mjs";

const BASE = process.argv[2] ?? "http://127.0.0.1:8000";
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const OUT = resolve(ROOT, "docs/story");

const USERS = {
  coordinator: { id: "coord-1", name: "Ирина Соколова", role: "coordinator" },
  reviewer: { id: "rev-2", name: "Ольга Дьяченко", role: "reviewer" },
  student: { id: "stu-1", name: "Анна Лебедева", role: "student" },
};

mkdirSync(OUT, { recursive: true });

const browser = await launchBrowser();
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 2, // печать: на 1× текст в скриншоте расплывается
  locale: "ru-RU",
});
const page = await context.newPage();

await page.addInitScript(() => {
  localStorage.setItem("avito_reviewer_theme", "light");
});

// Шапка приложения липкая (`position: sticky`). При съёмке отдельного блока
// Playwright прокручивает страницу к нему, и шапка оказывается поверх кадра —
// поперёк карточки шла полоса с часами и кнопкой «Выйти». Для съёмки она
// делается обычной: в документе она всё равно не нужна.
const UNSTICK = "header { position: static !important; }";

let taken = 0;

/** Входит под ролью и ждёт, пока подтянутся данные. */
async function login(role, view) {
  await page.addInitScript(
    (u) => sessionStorage.setItem("avito_reviewer_user", JSON.stringify(u)),
    USERS[role],
  );
  await page.goto(`${BASE}/?t=${Date.now()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  await page.addStyleTag({ content: UNSTICK });
  if (view) {
    await page.getByRole("button", { name: view, exact: true }).click();
    await page.waitForTimeout(1800);
  }
}

/** Раскрывает секцию по заголовку, если она свёрнута. */
async function open(title) {
  const head = page.locator(`.card > div > button:has-text("${title}")`).first();
  if ((await head.count()) === 0) return false;
  const label = (await head.textContent()) ?? "";
  if (label.trim().startsWith("▸")) {
    await head.click();
    await page.waitForTimeout(900);
  }
  return true;
}

/** Снимает карточку секции целиком. */
async function shot(file, title, { caption, inner } = {}) {
  if (title && !(await open(title))) {
    console.log(`  ${file}.png — пропущен: нет секции «${title}»`);
    return;
  }
  const card = title
    ? page.locator(`.card:has(> div > button:has-text("${title}"))`).first()
    : page.locator("main");
  // `inner` берёт часть секции. Пять критериев подряд дают кадр высотой
  // четыре тысячи пикселей — на листе A4 это нечитаемо; для рассказа
  // достаточно одного критерия целиком.
  const target = inner ? card.locator(inner).first() : card;
  await target.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  await target.screenshot({ path: resolve(OUT, `${file}.png`) });
  taken += 1;
  console.log(`  ${file}.png${caption ? "  — " + caption : ""}`);
}

// ── экран входа ───────────────────────────────────────────────────────────────
await page.addInitScript(() => sessionStorage.removeItem("avito_reviewer_user"));
await page.goto(`${BASE}/?t=${Date.now()}`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await page.screenshot({ path: resolve(OUT, "01-login.png"), fullPage: true });
taken += 1;
console.log("  01-login.png");

// ── методист ──────────────────────────────────────────────────────────────────
await login("coordinator");
await shot("02-rubric", "Критерии оценивания", { caption: "критерии извлечены из условия" });
await shot("03-upload", "Загрузка работ", { caption: "загрузка и повторная сдача" });
await shot("04-people", "Участники", { caption: "заведение пользователей" });
await shot("05-deadlines", "Сроки и штрафы", { caption: "три срока, пресеты" });
await shot("06-allocation", "Распределение по ревьюерам", { caption: "нагрузка до/после" });
await shot("07-works", "Работы", { caption: "поток и выгрузка" });
await shot("08-scoring", "Формула приоритета ручной проверки", { caption: "веса и пресеты" });

// ── ревьюер ───────────────────────────────────────────────────────────────────
await login("reviewer");
// Очередь — левая колонка экрана; отдельной секцией она не оформлена.
//
// `align-self: start` обязателен только для съёмки: элемент сетки по
// умолчанию растягивается на высоту строки, то есть на высоту всей карточки
// работы рядом. В приложении это незаметно (фона у колонки нет), а в кадр
// попадали четыре тысячи пустых пикселей — в документе шаг занимал десять
// страниц, из которых восемь были пустыми.
await page.addStyleTag({ content: "aside { align-self: start !important; }" });
const queue = page.locator("aside").first();
await queue.screenshot({ path: resolve(OUT, "08b-queue.png") });
taken += 1;
console.log("  08b-queue.png  — очередь по индексу приоритета");
await shot("09-formal", "Формальные проверки", { caption: "слой без модели" });
await shot("10-criteria", "Оценка по критериям", {
  caption: "один критерий целиком: балл, вердикт, цитаты, пробелы",
  inner: "div.border-b",
});
await shot("11-ai", "Признаки генеративного ИИ", { caption: "сигнал с основаниями" });
await shot("12-viewer", "Документ с подсветкой", { caption: "четыре слоя" });
await shot("13-trace", "Как шла проверка", { caption: "что отработало" });
// Блок подтверждения — не сворачиваемая секция, а подвал карточки работы.
const confirm = page.locator('.card:has-text("Итоговое решение")').last();
if (await confirm.count()) {
  await confirm.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  await confirm.screenshot({ path: resolve(OUT, "14-confirm.png") });
  taken += 1;
  console.log("  14-confirm.png  — решает человек");
}

// ── студент ───────────────────────────────────────────────────────────────────
await login("student");
// Только первая карточка работы: экран студента целиком — четыре с половиной
// тысячи пикселей, на листе от него остаётся полоска.
const work = page.locator("main .card").first();
await work.scrollIntoViewIfNeeded();
await page.waitForTimeout(400);
await work.screenshot({ path: resolve(OUT, "15-student.png") });
taken += 1;
console.log("  15-student.png  — подтверждённый балл и фидбек");
await shot("16-profile", "Мой прогресс", { caption: "динамика и радар" });

// ── методист: итоги ───────────────────────────────────────────────────────────
await login("coordinator");
await shot("17-analytics", "Аналитика потока", { caption: "воронка и баллы" });
await shot("18-effect", "Эффект: измеримые показатели", { caption: "метрики кейса" });
await shot("19-gaps", "Типовые ошибки потока", { caption: "материалы курса" });

await login("coordinator", "Качество");
await page.locator("main").screenshot({ path: resolve(OUT, "20-quality.png") });
taken += 1;
console.log("  20-quality.png");

await login("coordinator", "Приватность");
await page.locator("main").screenshot({ path: resolve(OUT, "21-privacy.png") });
taken += 1;
console.log("  21-privacy.png");

await browser.close();
console.log(`\nГотово: ${taken} кадров в docs/story/`);
