import type { NodeContext, PartInstance, SysmlMember } from './types';

const contexts = {
  ingress: { id: 'ingress', label: 'Ingress', column: 0, row: 0 },
  application: { id: 'application', label: 'Application', column: 1, row: 0 },
  aiSearch: { id: 'aiSearch', label: 'AI & Search', column: 2, row: 0 },
  metadata: { id: 'metadata', label: 'Metadata', column: 2, row: 1 },
  dataStorage: { id: 'dataStorage', label: 'Data & Storage', column: 3, row: 1 },
  identity: { id: 'identity', label: 'Identity & Secrets', column: 1, row: 1 },
  messaging: { id: 'messaging', label: 'Messaging', column: 1, row: 2 },
  platform: { id: 'platform', label: 'AWS Platform', column: 0, row: 1 },
  external: { id: 'external', label: 'External Services', column: 3, row: 0 },
  operations: { id: 'operations', label: 'Ops & Backup', column: 3, row: 2 },
  other: { id: 'other', label: 'Other', column: 2, row: 2 },
} satisfies Record<string, NodeContext>;

type ContextKey = keyof typeof contexts;

const metadataContextByValue: Record<string, ContextKey> = {
  ai: 'aiSearch',
  application: 'application',
  app: 'application',
  auth: 'identity',
  backup: 'operations',
  broker: 'messaging',
  cache: 'dataStorage',
  cloud: 'platform',
  data: 'dataStorage',
  database: 'dataStorage',
  datastore: 'dataStorage',
  db: 'dataStorage',
  external: 'external',
  identity: 'identity',
  ingress: 'ingress',
  internal: 'application',
  messaging: 'messaging',
  metadata: 'metadata',
  network: 'ingress',
  observability: 'operations',
  operations: 'operations',
  platform: 'platform',
  queue: 'messaging',
  search: 'aiSearch',
  secret: 'identity', //pragma: allowlist secret
  secrets: 'identity', //pragma: allowlist secret
  service: 'application',
  storage: 'dataStorage',
  'third-party': 'external',
  vector: 'aiSearch',
};

const technologyContextByValue: Record<string, ContextKey> = {
  alb: 'ingress',
  amqp: 'messaging',
  aws: 'platform',
  'aws-alb': 'ingress',
  'aws-backup': 'operations',
  cloudwatch: 'operations',
  ebs: 'dataStorage',
  ecr: 'platform',
  efs: 'dataStorage',
  eks: 'platform',
  elasticsearch: 'aiSearch',
  iam: 'identity',
  keycloak: 'identity',
  keyvault: 'identity',
  milvus: 'aiSearch',
  nfs: 'dataStorage',
  opensearch: 'aiSearch',
  postgres: 'dataStorage',
  postgresql: 'dataStorage',
  rabbitmq: 'messaging',
  redis: 'dataStorage',
  route53: 'ingress',
  s3: 'dataStorage',
  vpc: 'platform',
};

const metadataAttributePriority = ['domain', 'context', 'category', 'layer', 'kind', 'type'];

const legacyFallbackRules: Array<{ context: ContextKey; terms: string[] }> = [
  { context: 'ingress', terms: ['users', 'route53', 'alb', 'ingress', 'internetgateway', 'acm', 'certificate'] },
  { context: 'metadata', terms: ['learninghive', 'openmetadata', 'airflow', 'discoveryengine', 'enricherengine'] },
  { context: 'identity', terms: ['keycloak', 'secrets', 'iamroles', 'securitygroups', 'azure_keyvault', 'keyvault'] },
  { context: 'messaging', terms: ['rabbitmq', 'amqp', 'notifications'] },
  { context: 'operations', terms: ['cloudwatch', 'logs', 'metrics', 'aws_backup', 'backupplan', 's3_backups'] },
  { context: 'platform', terms: ['eks', 'vpc', 'natgateway', 'ecr'] },
  { context: 'dataStorage', terms: ['postgresql', 'efs', 'ebs', 's3', 'storage', 'database', 'backupobjects'] },
  { context: 'aiSearch', terms: ['deepsearch', 'rag_api', 'searxng', 'crawl4ai', 'milvus', 'opensearch'] },
  {
    context: 'external',
    terms: ['openai', 'embedding_service', 'reranker_service', 'sourcesystems', 'source systems', 'external'],
  },
  {
    context: 'application',
    terms: ['frontend', 'ui', 'backend', 'api', 'service', 'worker', 'app', 'docs'],
  },
];

export function classifyInstance(instance: PartInstance): NodeContext {
  return classifyFromMetadata(instance) ?? classifyFromLegacyText(instance) ?? contexts.other;
}

function classifyFromMetadata(instance: PartInstance): NodeContext | undefined {
  const attributes = instance.definition?.attributes ?? [];

  for (const name of metadataAttributePriority) {
    const value = attributeValue(attributes, name);
    const context = value ? contextForMetadataValue(value, name) : undefined;
    if (context) {
      return contexts[context];
    }
  }

  return undefined;
}

function contextForMetadataValue(value: string, attributeName: string): ContextKey | undefined {
  const values = normalizedTokens(value);
  const contextMap = attributeName === 'type' ? technologyContextByValue : metadataContextByValue;

  for (const token of values) {
    const context = contextMap[token] ?? metadataContextByValue[token] ?? technologyContextByValue[token];
    if (context) {
      return context;
    }
  }

  return undefined;
}

function classifyFromLegacyText(instance: PartInstance): NodeContext | undefined {
  const text = `${instance.name} ${instance.type} ${instance.definition?.doc ?? ''}`.toLowerCase();
  const rule = legacyFallbackRules.find((candidate) => matches(text, candidate.terms));
  return rule ? contexts[rule.context] : undefined;
}

function attributeValue(attributes: SysmlMember[], name: string): string | undefined {
  const attribute = attributes.find((candidate) => candidate.name.toLowerCase() === name.toLowerCase());
  return attribute?.value?.replace(/^["']|["']$/g, '');
}

function normalizedTokens(value: string): string[] {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/_/g, '-')
    .replace(/[^a-z0-9-]+/g, ' ')
    .trim();
  const splitTokens = normalized.split(/\s+/).filter(Boolean);
  return [normalized, ...splitTokens];
}

function matches(text: string, terms: string[]) {
  return terms.some((term) => text.includes(term));
}
