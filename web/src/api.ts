/**
 * Клиент API и подписка на события.
 *
 * Идентификатор пользователя хранится в `sessionStorage`, а не в
 * `localStorage`: `sessionStorage` изолирован по вкладке, поэтому три
 * вкладки одного браузера живут под тремя разными ролями одновременно.
 * Ровно это и нужно для демонстрации — без режима инкогнито.
 */

const KEY = "avito_reviewer_user";

export type Role = "coordinator" | "reviewer" | "student";

export interface User {
  id: string;
  name: string;
  role: Role;
  capacity?: number;
  competencies?: string[];
}

export function getUser(): User | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as User) : null;
  } catch {
    return null;
  }
}

export function setUser(u: User | null): void {
  if (u) sessionStorage.setItem(KEY, JSON.stringify(u));
  else sessionStorage.removeItem(KEY);
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public hint?: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const user = getUser();
  const headers = new Headers(init.headers);
  if (user) headers.set("X-User-Id", user.id);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`/api${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = `Ошибка ${res.status}`;
    let hint: string | undefined;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
      hint = body.hint;
    } catch {
      /* тело не JSON — оставляем код статуса */
    }
    throw new ApiError(detail, res.status, hint);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  health: () => request<any>("/health"),
  privacy: () => request<any>("/privacy"),
  login: (login: string, password: string) =>
    request<User>("/login", {
      method: "POST",
      body: JSON.stringify({ login, password }),
    }),
  register: (body: Record<string, unknown>) =>
    request<User>("/register", { method: "POST", body: JSON.stringify(body) }),
  registrationRoles: () => request<any>("/registration-roles"),
  demoCredentials: () => request<any>("/demo-credentials"),
  users: () => request<User[]>("/users"),
  tracks: () => request<any>("/tracks"),
  createUser: (body: Record<string, unknown>) =>
    request<any>("/users", { method: "POST", body: JSON.stringify(body) }),
  updateUser: (id: string, body: Record<string, unknown>) =>
    request<any>(`/users/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deactivateUser: (id: string) =>
    request<any>(`/users/${id}/deactivate`, { method: "POST" }),

  assignments: () => request<any[]>("/assignments"),
  createAssignment: (body: Record<string, unknown>) =>
    request<any>("/assignments", { method: "POST", body: JSON.stringify(body) }),
  assignment: (id: string) => request<any>(`/assignments/${id}`),
  uploadCondition: (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<any>(`/assignments/${id}/condition`, { method: "POST", body: fd });
  },
  uploadConditionText: (id: string, text: string) =>
    request<any>(`/assignments/${id}/condition/text`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  updateRubric: (id: string, rubric: any) =>
    request<any>(`/assignments/${id}/rubric`, {
      method: "PUT",
      body: JSON.stringify({ rubric }),
    }),
  approveRubric: (id: string) =>
    request<any>(`/assignments/${id}/rubric/approve`, { method: "POST" }),

  setDeadlines: (id: string, body: Record<string, unknown>) =>
    request<any>(`/assignments/${id}/deadlines`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  setNormalDeadlines: (id: string) =>
    request<any>(`/assignments/${id}/deadlines/normal`, { method: "POST" }),
  setDemoDeadlines: (id: string, softS: number, hardS: number) =>
    request<any>(`/assignments/${id}/deadlines/demo?soft_s=${softS}&hard_s=${hardS}`, {
      method: "POST",
    }),

  submissions: (assignmentId?: string) =>
    request<any[]>(`/submissions${assignmentId ? `?assignment_id=${assignmentId}` : ""}`),
  submission: (id: string) => request<any>(`/submissions/${id}`),
  uploadSubmission: (fd: FormData) =>
    request<any>("/submissions", { method: "POST", body: fd }),

  allocate: (assignmentId: string) =>
    request<any>(`/assignments/${assignmentId}/allocate`, { method: "POST" }),
  reassign: (submissionId: string, reviewerId: string) =>
    request<any>(`/submissions/${submissionId}/reviewer`, {
      method: "PUT",
      body: JSON.stringify({ reviewer_id: reviewerId }),
    }),

  startReview: (id: string) => request<any>(`/submissions/${id}/review`, { method: "POST" }),
  reviewAll: (assignmentId: string) =>
    request<any>(`/assignments/${assignmentId}/review-all`, { method: "POST" }),

  confirm: (id: string, body: Record<string, unknown>) =>
    request<any>(`/submissions/${id}/confirm`, { method: "POST", body: JSON.stringify(body) }),
  aiVerdict: (id: string, verdict: string, comment: string) =>
    request<any>(`/submissions/${id}/ai-verdict`, {
      method: "POST",
      body: JSON.stringify({ verdict, comment }),
    }),

  similarity: (assignmentId: string) => request<any>(`/assignments/${assignmentId}/similarity`),
  scoring: () => request<any>("/scoring"),
  setScoring: (assignmentId: string, body: Record<string, unknown>) =>
    request<any>(`/scoring?assignment_id=${assignmentId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  presets: () => request<any[]>("/scoring/presets"),
  savePreset: (name: string) =>
    request<any>("/scoring/presets", { method: "POST", body: JSON.stringify({ name }) }),
  applyPreset: (name: string, assignmentId?: string) =>
    request<any>(
      `/scoring/presets/apply${assignmentId ? `?assignment_id=${assignmentId}` : ""}`,
      { method: "POST", body: JSON.stringify({ name }) },
    ),
  deletePreset: (name: string) =>
    request<any>(`/scoring/presets?name=${encodeURIComponent(name)}`, { method: "DELETE" }),

  analytics: (assignmentId?: string) =>
    request<any>(`/analytics${assignmentId ? `?assignment_id=${assignmentId}` : ""}`),
  studentProfile: (studentId: string) => request<any>(`/students/${studentId}/profile`),
  quality: () => request<any>("/quality"),

  notifications: () => request<any[]>("/notifications"),
  markRead: () => request<any>("/notifications/read", { method: "POST" }),

  reset: () => request<any>("/demo/reset", { method: "POST" }),

  exportUrl: (assignmentId: string, fmt: string, history = false) =>
    `/api/assignments/${assignmentId}/export?fmt=${fmt}` + (history ? "&history=true" : ""),
  exportReviewUrl: (submissionId: string, fmt: string) =>
    `/api/submissions/${submissionId}/export?fmt=${fmt}`,
  /** Исходный файл работы. Путь на диске сервер берёт сам, по идентификатору. */
  originalFileUrl: (submissionId: string) => `/api/submissions/${submissionId}/file`,
};

/**
 * Подписка на серверные события.
 *
 * `EventSource` не умеет задавать заголовки, поэтому идентификатор
 * передаётся параметром запроса. Переподключение при обрыве делает
 * сам браузер — отдельная логика ретраев не нужна.
 */
export function subscribe(
  user: User,
  handlers: Record<string, (data: any) => void>,
): () => void {
  const src = new EventSource(
    `/api/events?user_id=${encodeURIComponent(user.id)}&role=${user.role}`,
  );
  const listeners: [string, EventListener][] = [];

  for (const [event, fn] of Object.entries(handlers)) {
    const listener = ((e: MessageEvent) => {
      try {
        fn(JSON.parse(e.data));
      } catch {
        /* некорректное событие не должно ронять интерфейс */
      }
    }) as EventListener;
    src.addEventListener(event, listener);
    listeners.push([event, listener]);
  }

  return () => {
    for (const [event, listener] of listeners) src.removeEventListener(event, listener);
    src.close();
  };
}

/** Скачивание файла с передачей заголовка авторизации. */
export async function download(url: string, filename: string): Promise<void> {
  const user = getUser();
  const res = await fetch(url, {
    headers: user ? { "X-User-Id": user.id } : {},
  });
  if (!res.ok) throw new ApiError(`Не удалось скачать (${res.status})`, res.status);
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(href);
}
