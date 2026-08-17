export type Artifact = {
  id: string;
  run_id: string;
  type: string;
  version: number;
  title: string;
  content: Record<string, unknown>;
  source_step: string;
  locked: boolean;
  created_by: string;
  created_at: string;
  parent_artifact_id?: string | null;
};

export type EventRecord = {
  id: string;
  run_id: string;
  step_id: string;
  level: string;
  actor: string;
  message: string;
  data: Record<string, unknown>;
  input_summary: Record<string, unknown>;
  output_summary: Record<string, unknown>;
  tool_calls: Array<Record<string, unknown>>;
  provider_mode: string;
  fallback_used: boolean;
  fallback_reason: string;
  timestamp: string;
};

export type RunRecord = {
  id: string;
  title: string;
  domain: string;
  problem_input: string;
  constraints: string;
  research_constraints?: Record<string, unknown>;
  research_constraints_artifact_id?: string | null;
  github_repository_url?: string | null;
  status: string;
  current_step: string;
  automatic: boolean;
  stop_requested: boolean;
  feedback_iteration: number;
  steps: Array<{
    id: string;
    name: string;
    status: string;
    started_at?: string | null;
    completed_at?: string | null;
    error?: Record<string, unknown> | null;
  }>;
  artifacts: Artifact[];
  events: EventRecord[];
  paper_writing?: PaperWritingState;
};

export type PaperWritingState = {
  status: string;
  stage: string;
  progress: number;
  config: {
    venue?: string;
    language?: "zh-CN" | "en";
    paper_type?: string;
    authors?: string;
    notes?: string;
  };
  plan: Record<string, unknown>;
  sections: Array<{
    id?: string;
    title?: string;
    content?: string;
    citations?: string[];
  }>;
  audit: Record<string, unknown>;
  references?: Array<Record<string, unknown>>;
  active_skill: string;
  current_section: string;
  completed_sections: number;
  total_sections: number;
  stop_requested: boolean;
  error: string;
  started_at: string;
  updated_at: string;
  completed_at: string;
};

export type ProviderComponentStatus = {
  mode: string;
  ready: boolean;
  code?: string;
  warning?: string;
  missing?: string[];
  workdir?: string;
  resolved_workdir?: string;
  entrypoint?: string;
  host?: string;
  user?: string;
  port?: number;
  project_dir?: string;
  stdout_tail?: string;
  stderr_tail?: string;
};

export type ModelProviderConfig = {
  provider_id: string;
  provider_type: string;
  display_name: string;
  base_url: string;
  api_key: string;
  models: string[];
  enabled: boolean;
  configured: boolean;
  connection_policy: { timeout_seconds?: number };
};

export type ModelRoleConfig = Record<string, { provider_id: string; model: string }>;

export type ProviderStatus = {
  llm?: ProviderComponentStatus;
  literature?: ProviderComponentStatus;
  experiment?: ProviderComponentStatus;
  model_providers?: ModelProviderConfig[];
};

export type ExperimentSettings = {
  provider: "remote_gpu" | "local_gpu" | "mock";
  remote: {
    host: string;
    user: string;
    port: number;
    ssh_key_path: string;
    project_dir: string;
    python: string;
    cuda_visible_devices: string;
    timeout_seconds: number;
  };
  local: {
    enabled: boolean;
    workdir: string;
    python: string;
    cuda_visible_devices: string;
    timeout_seconds: number;
  };
  dataset: {
    source: "auto" | "auto_local" | "official" | "online" | "local";
    dir: string;
    mirror_url: string;
    download_retries: number;
  };
};

export type ExperimentProgress = {
  state: "idle" | "running" | "completed" | "failed" | "timed_out" | "terminated" | "orphaned" | "stalled" | "unknown";
  run_id?: string;
  experiment_id?: string;
  phase?: string;
  pid?: number;
  started_at?: string;
  updated_at?: string;
  elapsed_seconds?: number;
  timeout_seconds?: number;
  log_bytes?: number;
  result_ready?: boolean;
  process_alive?: boolean;
  heartbeat_age_seconds?: number;
  healthy?: boolean;
  attempt_id?: string;
  log_tail?: string;
  gpu?: {
    utilization_percent?: number;
    memory_used_mb?: number;
    memory_total_mb?: number;
    temperature_c?: number;
  };
};

export type ExperimentTestResult = {
  ok: boolean;
  provider: string;
  code?: string;
  missing: string[];
  workdir?: string;
  resolved_workdir?: string;
  entrypoint?: string;
  host?: string;
  user?: string;
  port?: number;
  project_dir?: string;
  python?: string;
  python_version?: string;
  torch_version?: string;
  torch_cuda?: string;
  cuda_available?: boolean;
  device_count?: number;
  device_names?: string[];
  available_device_indexes?: number[];
  dependency_status?: string;
  stdout_tail?: string;
  stderr_tail?: string;
  dataset_profile?: {
    contract_id: string;
    root: string;
    file_count: number;
    content_fingerprint: string;
  };
  message: string;
};

export type LocalLiteratureDocument = {
  id: string;
  filename: string;
  media_type: string;
  sha256: string;
  size_bytes: number;
  title: string;
  authors: string[];
  year?: number | null;
  abstract: string;
  identifiers: Record<string, string>;
  statuses: string[];
  verification: {
    verified: boolean;
    provider: string;
    verified_at?: string | null;
  };
  wiki_node_id?: string | null;
  run_ids: string[];
};
