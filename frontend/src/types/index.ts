export type ProxyProtocol = 'hysteria2' | 'tuic' | 'shadowsocks';

export interface AdminUser {
  id: number | string;
  username: string;
  must_change_password?: boolean;
  password_change_required?: boolean;
  last_login?: string | null;
}

export interface LoginResponse {
  user?: AdminUser;
  must_change_password?: boolean;
  password_change_required?: boolean;
  csrf_token?: string;
}

export interface SingboxSummary {
  status: string;
  version?: string | null;
  pid?: number | null;
  uptime_seconds?: number | null;
}

export interface SystemStatus {
  cpu_percent: number;
  memory_percent: number;
  memory_used_mb: number;
  memory_total_mb: number;
  disk_percent: number;
  disk_used_gb?: number;
  disk_total_gb?: number;
  uptime_seconds: number;
  singbox: SingboxSummary;
  nodes?: Array<{
    id: number | string;
    name: string;
    protocol: ProxyProtocol;
    enabled: boolean;
    status: string;
  }>;
}

export type NodeConfig = Record<string, unknown>;

export interface ProxyNode {
  id: number | string;
  name: string;
  protocol: ProxyProtocol;
  enabled: boolean;
  listen_port: number;
  config_json: NodeConfig | string | null;
  status?: string;
  created_at?: string;
  updated_at?: string;
}

export interface NodePayload {
  name: string;
  protocol: ProxyProtocol;
  enabled: boolean;
  listen_port: number;
  config_json: NodeConfig;
}

export interface Settings {
  id?: number | string;
  server_name: string;
  server_ipv4: string | null;
  domain: string | null;
  certificate_path: string | null;
  private_key_path: string | null;
  self_signed_mode?: boolean;
  updated_at?: string;
}

export interface SubscriptionInfo {
  token?: string;
  mihomo_url: string;
  provider_url: string;
  expires_at?: string | null;
  self_signed_mode?: boolean;
}

export interface BackupInfo {
  name: string;
  created_at?: string;
  size_bytes?: number;
}

export interface ApiMessage {
  message?: string;
  detail?: string;
  success?: boolean;
}

export interface ListResponse<T> {
  items?: T[];
  nodes?: T[];
  backups?: T[];
}

export interface LogsResponse {
  lines?: string[];
  logs?: string[] | string;
  content?: string;
}
