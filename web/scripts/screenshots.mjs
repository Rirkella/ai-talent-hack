/**
 * Съёмка скриншотов интерфейса для документации и презентации.
 *
 * Инструмент разработки, а не часть приложения: playwright лежит в
 * devDependencies и на демонстрации не нужен. Запускается при работающем
 * сервере и раскладывает PNG в docs/screenshots/.
 *
 *   cd web && npm run shots
 *
 * Скрипт лежит внутри web/, потому что ESM ищет пакеты относительно файла:
 * из корня проекта импорт playwright не разрешается.
 *
 * Роль подставляется прямо в sessionStorage — тем же способом, которым её
 * кладёт экран входа. Так съёмка не зависит от кликов по кнопкам и не
 * ломается при правке разметки логина.
 */

import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { launchBrowser } from "./launch_browser.mjs";

const BASE = process.argv[2] ?? "http://127.0.0.1:8000";
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const OUT = resolve(ROOT, "docs/screenshots");

// Пользователи демо-данных. Идентификаторы стабильны — их задаёт app/seed.py.
const USERS = {
  coordinator: { id: "coord-1", name: "Ирина Соколова", role: "coordinator" },
  reviewer: { id: "rev-2", name: "Ольга Дьяченко", role: "reviewer" },
  student: { id: "stu-1", name: "Анна Лебедева", role: "student" },
};

// fullPage вместо гигантского вьюпорта: при высоте окна, сильно превышающей
// контент, композитор дублирует плитки, и шапка отрисовывалась дважды.
const SHOTS = [
  { file: "01-login-dark", user: null, theme: "dark" },
  { file: "02-login-light", user: null, theme: "light" },
  { file: "03-coordinator-dark", user: "coordinator", theme: "dark" },
  { file: "04-coordinator-light", user: "coordinator", theme: "light" },
  { file: "05-reviewer-dark", user: "reviewer", theme: "dark" },
  { file: "06-reviewer-light", user: "reviewer", theme: "light" },
  { file: "07-student-dark", user: "student", theme: "dark" },
  { file: "08-student-light", user: "student", theme: "light" },
  // Вкладка «Приватность» — финальный кадр демонстрации.
  { file: "09-privacy-dark", user: "coordinator", theme: "dark", view: "privacy" },
  // Вкладка «Качество» — цифры бенчмарка, включая неудачную пару.
  { file: "10-quality-dark", user: "coordinator", theme: "dark", view: "Качество" },
  { file: "11-quality-light", user: "coordinator", theme: "light", view: "Качество" },
  // Повторная сдача: карточка сравнения версий у ревьюера.
  {
    file: "12-revision-dark", user: "reviewer", theme: "dark",
    openWork: "Анна Лебедева", requires: "сравнение с предыдущей",
  },
];

mkdirSync(OUT, { recursive: true });

const browser = await launchBrowser();
let taken = 0;

for (const shot of SHOTS) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    locale: "ru-RU",
  });
  const page = await context.newPage();

  // Тема и роль выставляются до первой отрисовки, иначе кадр поймает
  // светлую тему на миг до применения класса.
  await page.addInitScript(
    ({ user, theme }) => {
      localStorage.setItem("avito_reviewer_theme", theme);
      if (user) sessionStorage.setItem("avito_reviewer_user", JSON.stringify(user));
      else sessionStorage.removeItem("avito_reviewer_user");
    },
    { user: shot.user ? USERS[shot.user] : null, theme: shot.theme },
  );

  await page.goto(BASE, { waitUntil: "networkidle" });
  // Данные приходят отдельными запросами после монтирования.
  await page.waitForTimeout(2500);

  // Вкладка внутри роли переключается кликом: отдельного адреса у неё нет,
  // потому что приложение однострочное и маршрутизатор ему не нужен.
  if (shot.view) {
    const label = shot.view === "privacy" ? "Приватность" : shot.view;
    await page.getByRole("button", { name: label }).click();
    await page.waitForTimeout(2000);
  }

  // Карточка работы открывается кликом по очереди: у неё нет своего адреса.
  // Берётся последняя подходящая карточка — очередь отсортирована по
  // приоритету, и повторная сдача без ревью стоит в ней ниже проверенных.
  if (shot.openWork) {
    const card = page.locator(`button.card:has-text("${shot.openWork}")`).last();
    if (await card.count()) {
      await card.click();
      await page.waitForTimeout(2500);
    }
  }

  // Кадр повторной сдачи требует, чтобы вторая версия работы существовала.
  // На чистых демо-данных её нет: файла доработанной работы среди примеров
  // не выдано, а подставить вместо неё чужое решение нельзя — детектор
  // схожести честно покажет 100 %, и получится ложная история о списывании.
  // Проверяется наличие самой карточки сравнения, а не работы студента:
  // без неё кадр повторил бы обычный экран ревьюера под чужим именем.
  if (shot.requires) {
    const marker = page.locator(`button:has-text("${shot.requires}")`);
    if ((await marker.count()) === 0) {
      console.log(`  ${shot.file}.png — пропущен: в данных нет повторной сдачи`);
      await context.close();
      continue;
    }
  }

  const path = resolve(OUT, `${shot.file}.png`);
  await page.screenshot({ path, fullPage: true });
  console.log(`  ${shot.file}.png  (${shot.theme}, ${shot.user ?? "экран входа"})`);
  taken += 1;

  await context.close();
}

await browser.close();
console.log(`\nГотово: ${taken} скриншотов в docs/screenshots/`);
