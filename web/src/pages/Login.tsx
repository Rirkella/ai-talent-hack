/**
 * Стартовая страница: вход по логину и паролю, рядом — регистрация.
 *
 * Роль на входе НЕ выбирается: выбор роли в форме означал бы, что войти
 * методистом может любой, кто нажал нужную кнопку. Роль определяется
 * учётной записью. В списке демонстрационных доступов роль подписана —
 * этого достаточно, чтобы понять, под кем входишь.
 *
 * Сессия хранится в `sessionStorage`: он изолирован по вкладке, поэтому
 * три вкладки одного браузера держат три роли одновременно.
 */

import { useEffect, useState } from "react";

import { api, type User } from "../api";

const ROLE_TITLE: Record<string, string> = {
  coordinator: "Методист",
  reviewer: "Ревьюер",
  student: "Студент",
};

const ROLE_DESC: Record<string, string> = {
  coordinator: "Критерии и сроки, распределение по ревьюерам, аналитика, выгрузка",
  reviewer: "Очередь работ, проверка с доказательствами, признаки ИИ, подтверждение",
  student: "Сдача работ, сроки и обратная связь после подтверждения ревьюером",
};

export default function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [tab, setTab] = useState<"login" | "register">("login");

  return (
    <div className="mx-auto flex min-h-full max-w-md flex-col justify-center p-6">
      <div className="mb-6 text-center">
        <h1 className="text-2xl font-semibold">
          Avito <span style={{ color: "var(--brand)" }}>AI Reviewer</span>
        </h1>
        <p className="muted mt-1 text-sm">
          Помощник ревьюера и координатора образовательных программ
        </p>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-1">
        {(
          [
            ["login", "Вход"],
            ["register", "Регистрация"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            className="btn"
            onClick={() => setTab(key)}
            style={
              tab === key ? { borderColor: "var(--brand)", color: "var(--brand)" } : undefined
            }
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "login" ? <LoginForm onLogin={onLogin} /> : <RegisterForm onLogin={onLogin} />}

      <p className="muted mt-4 text-center text-xs">
        Чтобы работать сразу тремя ролями, откройте приложение в трёх вкладках
        и войдите в каждой по-своему: сессия у вкладки своя.
      </p>
      <p className="muted mt-1 text-center text-xs">
        Пароль хранится хешем <code>pbkdf2</code>, но это форма входа, а не
        рубеж обороны: контур защищён тем, что приложение не выходит в сеть.
      </p>
    </div>
  );
}

function LoginForm({ onLogin }: { onLogin: (u: User) => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState<any>(null);
  const [showAccounts, setShowAccounts] = useState(false);

  // Демонстрационные доступы. На реальном внедрении выключаются ключом
  // DEMO_SHOW_CREDENTIALS — тогда блок не показывается вовсе.
  useEffect(() => {
    api
      .demoCredentials()
      .then((h) => {
        setHint(h);
        // Первый доступ подставляем сразу: на защите это экономит печать,
        // а поля остаются обычными полями ввода.
        if (h?.password && h.accounts?.length) {
          setLogin(h.accounts[0].login);
          setPassword(h.password);
        }
      })
      .catch(() => setHint(null));
  }, []);

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!login.trim() || !password) {
      setError("Введите логин и пароль");
      return;
    }
    setBusy(true);
    setError("");
    api
      .login(login.trim(), password)
      .then(onLogin)
      .catch((err) => setError(String(err.message ?? err)))
      .finally(() => setBusy(false));
  };

  const accounts: any[] = hint?.accounts ?? [];

  return (
    <form className="card" onSubmit={submit}>
      <div className="mb-3 font-semibold">Вход в профиль</div>

      <label className="mb-2 block">
        <span className="muted mb-0.5 block text-xs">Логин</span>
        <input
          className="input"
          autoFocus
          autoComplete="username"
          placeholder="familiya.i"
          value={login}
          onChange={(e) => setLogin(e.target.value)}
        />
      </label>

      <label className="mb-3 block">
        <span className="muted mb-0.5 block text-xs">Пароль</span>
        <input
          className="input"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>

      {error && (
        <div
          className="mb-3 rounded p-2 text-sm"
          style={{ background: "var(--surface-2)", color: "var(--err)" }}
        >
          {error}
        </div>
      )}

      <button className="btn btn-primary w-full" type="submit" disabled={busy}>
        {busy ? "Вход…" : "Войти"}
      </button>

      {hint?.password && (
        <div className="mt-3 border-t pt-3" style={{ borderColor: "var(--border)" }}>
          <button
            type="button"
            className="muted text-xs"
            onClick={() => setShowAccounts((v) => !v)}
          >
            {showAccounts ? "▾" : "▸"} Демонстрационные доступы ({accounts.length})
          </button>
          {showAccounts && (
            <div className="mt-2">
              <div className="muted mb-1 text-xs">
                Пароль у всех: <code>{hint.password}</code>. Нажмите строку, чтобы
                подставить логин.
              </div>
              <div className="flex max-h-64 flex-col gap-0.5 overflow-auto">
                {accounts.map((a) => (
                  <button
                    key={a.login}
                    type="button"
                    className="flex items-center justify-between gap-2 rounded px-1.5 py-1 text-left text-xs"
                    style={{ background: "var(--surface-2)" }}
                    onClick={() => {
                      setLogin(a.login);
                      setPassword(hint.password);
                      setError("");
                    }}
                    title={ROLE_DESC[a.role]}
                  >
                    <span className="truncate">{a.name}</span>
                    <span className="flex shrink-0 items-center gap-2">
                      <span className="badge">{ROLE_TITLE[a.role] ?? a.role}</span>
                      <code className="muted">{a.login}</code>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </form>
  );
}

/**
 * Регистрация.
 *
 * Список ролей приходит с сервера: какие из них открыты для
 * самостоятельной регистрации, решает `.env`, а не интерфейс. На демо
 * открыты все три, в реальном внедрении остаётся студент — и тогда
 * переключатель ролей исчезает сам.
 */
function RegisterForm({ onLogin }: { onLogin: (u: User) => void }) {
  const [roles, setRoles] = useState<string[]>([]);
  const [role, setRole] = useState("student");
  const [name, setName] = useState("");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .registrationRoles()
      .then((r) => {
        setRoles(r.roles ?? []);
        if (r.roles?.length && !r.roles.includes("student")) setRole(r.roles[0]);
      })
      .catch(() => setRoles(["student"]));
  }, []);

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    setBusy(true);
    setError("");
    api
      .register({ name, role, login: login.trim(), password })
      .then(onLogin)
      .catch((err) => setError(String(err.message ?? err)))
      .finally(() => setBusy(false));
  };

  return (
    <form className="card" onSubmit={submit}>
      <div className="mb-1 font-semibold">Регистрация</div>
      <p className="muted mb-3 text-xs">
        После регистрации вход выполняется сразу — отдельного подтверждения нет.
      </p>

      <label className="mb-2 block">
        <span className="muted mb-0.5 block text-xs">Имя и фамилия</span>
        <input
          className="input"
          placeholder="Иван Петров"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>

      {roles.length > 1 && (
        <label className="mb-2 block">
          <span className="muted mb-0.5 block text-xs">Роль</span>
          <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {ROLE_TITLE[r] ?? r}
              </option>
            ))}
          </select>
          <span className="muted mt-0.5 block text-xs">{ROLE_DESC[role]}</span>
        </label>
      )}

      <label className="mb-2 block">
        <span className="muted mb-0.5 block text-xs">
          Логин — латиница, цифры, точка; от трёх символов
        </span>
        <input
          className="input"
          autoComplete="username"
          placeholder="petrov.i"
          value={login}
          onChange={(e) => setLogin(e.target.value)}
        />
      </label>

      <label className="mb-3 block">
        <span className="muted mb-0.5 block text-xs">Пароль — от шести символов</span>
        <input
          className="input"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>

      {error && (
        <div
          className="mb-3 rounded p-2 text-sm"
          style={{ background: "var(--surface-2)", color: "var(--err)" }}
        >
          {error}
        </div>
      )}

      <button className="btn btn-primary w-full" type="submit" disabled={busy}>
        {busy ? "Создание…" : "Зарегистрироваться и войти"}
      </button>

      <p className="muted mt-3 text-xs">
        На демонстрационном стенде открыта регистрация всеми тремя ролями,
        чтобы можно было попробовать любую. В реальном внедрении здесь
        остаётся только студент: роль ревьюера и методиста выдаёт человек,
        отвечающий за программу.
      </p>
    </form>
  );
}
