import axios, { AxiosError } from 'axios';
import type {
  AdminUser,
  ApiMessage,
  BackupInfo,
  ListResponse,
  LoginResponse,
  LogsResponse,
  NodePayload,
  ProxyNode,
  Settings,
  SubscriptionInfo,
  SystemStatus,
} from '../types';

let responseCsrfToken = '';
const APPLY_TIMEOUT_MS = 150_000;

function readCookie(name: string): string {
  const prefix = name + '=';
  const entry = document.cookie
    .split(';')
    .map((item) => item.trim())
    .find((item) => item.startsWith(prefix));

  return entry ? decodeURIComponent(entry.slice(prefix.length)) : '';
}

function getCsrfToken(): string {
  return (
    readCookie('XSRF-TOKEN') ||
    readCookie('csrf_token') ||
    readCookie('myproxy_csrf') ||
    responseCsrfToken
  );
}

export const apiClient = axios.create({
  baseURL: '/api/v1',
  timeout: 15_000,
  withCredentials: true,
  headers: {
    Accept: 'application/json',
  },
});

apiClient.interceptors.request.use((config) => {
  const method = (config.method ?? 'get').toLowerCase();
  if (!['get', 'head', 'options'].includes(method)) {
    const token = getCsrfToken();
    if (token) {
      config.headers.set('X-CSRF-Token', token);
    }
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => {
    const headerToken = response.headers['x-csrf-token'];
    const body = response.data as { csrf_token?: unknown } | undefined;
    if (typeof headerToken === 'string') {
      responseCsrfToken = headerToken;
    } else if (typeof body?.csrf_token === 'string') {
      responseCsrfToken = body.csrf_token;
    }
    return response;
  },
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      window.dispatchEvent(new CustomEvent('myproxy:unauthorized'));
    }
    return Promise.reject(error);
  },
);

function unwrap<T>(payload: T | { data: T }): T {
  if (
    payload &&
    typeof payload === 'object' &&
    !Array.isArray(payload) &&
    'data' in payload
  ) {
    return (payload as { data: T }).data;
  }
  return payload as T;
}

export function getApiError(error: unknown, fallback = '操作失败，请稍后重试'): string {
  if (!axios.isAxiosError(error)) {
    return error instanceof Error ? error.message : fallback;
  }

  const data = error.response?.data as
    | { detail?: unknown; message?: unknown; error?: unknown }
    | string
    | undefined;

  if (typeof data === 'string' && data.trim()) return data;
  if (data && typeof data === 'object') {
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail
        .map((item) => {
          if (typeof item === 'string') return item;
          if (item && typeof item === 'object' && 'msg' in item) return String(item.msg);
          return '';
        })
        .filter(Boolean)
        .join('；');
    }
    if (typeof data.message === 'string') return data.message;
    if (typeof data.error === 'string') return data.error;
  }

  if (error.code === 'ECONNABORTED') return '请求超时，请检查服务状态';
  if (!error.response) return '无法连接到管理服务';
  return fallback;
}

export function normalizeList<T>(payload: T[] | ListResponse<T>): T[] {
  if (Array.isArray(payload)) return payload;
  return payload.items ?? payload.nodes ?? payload.backups ?? [];
}

export const authApi = {
  async login(username: string, password: string): Promise<LoginResponse> {
    const response = await apiClient.post<LoginResponse | { data: LoginResponse }>('/auth/login', {
      username,
      password,
    });
    return unwrap(response.data);
  },

  async me(): Promise<AdminUser> {
    const response = await apiClient.get<AdminUser | { data: AdminUser }>('/auth/me');
    return unwrap(response.data);
  },

  async logout(): Promise<void> {
    await apiClient.post('/auth/logout');
    responseCsrfToken = '';
  },

  async changePassword(currentPassword: string, newPassword: string): Promise<ApiMessage> {
    const response = await apiClient.post<ApiMessage | { data: ApiMessage }>(
      '/auth/change-password',
      {
        current_password: currentPassword,
        new_password: newPassword,
      },
    );
    return unwrap(response.data);
  },
};

export const systemApi = {
  async status(): Promise<SystemStatus> {
    const response = await apiClient.get<SystemStatus | { data: SystemStatus }>('/system/status');
    return unwrap(response.data);
  },
};

export const nodeApi = {
  async list(): Promise<ProxyNode[]> {
    const response = await apiClient.get<ProxyNode[] | ListResponse<ProxyNode> | { data: ProxyNode[] }>('/nodes');
    return normalizeList(unwrap(response.data));
  },

  async create(payload: NodePayload): Promise<ProxyNode> {
    const response = await apiClient.post<ProxyNode | { data: ProxyNode }>('/nodes', payload, {
      timeout: APPLY_TIMEOUT_MS,
    });
    return unwrap(response.data);
  },

  async update(id: ProxyNode['id'], payload: NodePayload): Promise<ProxyNode> {
    const response = await apiClient.put<ProxyNode | { data: ProxyNode }>('/nodes/' + id, payload, {
      timeout: APPLY_TIMEOUT_MS,
    });
    return unwrap(response.data);
  },

  async remove(id: ProxyNode['id']): Promise<void> {
    await apiClient.delete('/nodes/' + id, { timeout: APPLY_TIMEOUT_MS });
  },

  async setEnabled(id: ProxyNode['id'], enabled: boolean): Promise<void> {
    await apiClient.post('/nodes/' + id + '/' + (enabled ? 'enable' : 'disable'), undefined, {
      timeout: APPLY_TIMEOUT_MS,
    });
  },

  async regenerateSecret(id: ProxyNode['id']): Promise<ApiMessage> {
    const response = await apiClient.post<ApiMessage | { data: ApiMessage }>(
      '/nodes/' + id + '/regenerate-secret',
      undefined,
      { timeout: APPLY_TIMEOUT_MS },
    );
    return unwrap(response.data);
  },
};

export const singboxApi = {
  async status() {
    const response = await apiClient.get('/singbox/status');
    return unwrap(response.data);
  },

  async check(): Promise<ApiMessage> {
    const response = await apiClient.post<ApiMessage | { data: ApiMessage }>(
      '/singbox/check',
      undefined,
      { timeout: APPLY_TIMEOUT_MS },
    );
    return unwrap(response.data);
  },

  async apply(): Promise<ApiMessage> {
    const response = await apiClient.post<ApiMessage | { data: ApiMessage }>(
      '/singbox/apply',
      undefined,
      { timeout: APPLY_TIMEOUT_MS },
    );
    return unwrap(response.data);
  },

  async restart(): Promise<ApiMessage> {
    const response = await apiClient.post<ApiMessage | { data: ApiMessage }>(
      '/singbox/restart',
      undefined,
      { timeout: APPLY_TIMEOUT_MS },
    );
    return unwrap(response.data);
  },

  async logs(lines = 200): Promise<LogsResponse | string[]> {
    const response = await apiClient.get<LogsResponse | string[] | { data: LogsResponse }>('/singbox/logs', {
      params: { lines },
    });
    return unwrap(response.data);
  },

  async backups(): Promise<BackupInfo[]> {
    const response = await apiClient.get<BackupInfo[] | ListResponse<BackupInfo> | { data: BackupInfo[] }>(
      '/singbox/backups',
    );
    return normalizeList(unwrap(response.data));
  },

  async restore(backup?: string): Promise<ApiMessage> {
    const response = await apiClient.post<ApiMessage | { data: ApiMessage }>('/singbox/restore', {
      backup,
    }, { timeout: APPLY_TIMEOUT_MS });
    return unwrap(response.data);
  },
};

export const subscriptionApi = {
  async info(): Promise<SubscriptionInfo> {
    const response = await apiClient.get<SubscriptionInfo | { data: SubscriptionInfo }>('/subscription');
    return unwrap(response.data);
  },

  async rotate(): Promise<SubscriptionInfo> {
    const response = await apiClient.post<SubscriptionInfo | { data: SubscriptionInfo }>(
      '/subscription/rotate',
    );
    return unwrap(response.data);
  },

  async preview(url: string): Promise<string> {
    const response = await axios.get<string>(url, {
      timeout: 15_000,
      withCredentials: false,
      responseType: 'text',
      headers: { Accept: 'text/yaml, text/plain, */*' },
    });
    return response.data;
  },
};

export const settingsApi = {
  async get(): Promise<Settings> {
    const response = await apiClient.get<Settings | { data: Settings }>('/settings');
    return unwrap(response.data);
  },

  async update(payload: Partial<Settings>): Promise<Settings> {
    const response = await apiClient.put<Settings | { data: Settings }>('/settings', payload);
    return unwrap(response.data);
  },

  async regenerateAllCredentials(): Promise<ApiMessage> {
    const response = await apiClient.post<ApiMessage | { data: ApiMessage }>(
      '/credentials/regenerate-all',
      undefined,
      { timeout: APPLY_TIMEOUT_MS },
    );
    return unwrap(response.data);
  },
};
