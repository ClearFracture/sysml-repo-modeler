import type { SysmlMember } from './types';

export function formatAttributeName(name: string) {
  return name.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, (value) => value.toUpperCase());
}

export function formatAttributeDisplay(attribute: SysmlMember) {
  return attribute.value ? formatEnumValue(attribute.value) : 'unset';
}

export function formatProtocol(attributes: SysmlMember[]) {
  const protocol = findAttributeValue(attributes, 'protocol');
  const transport = findAttributeValue(attributes, 'transportProtocol');
  const alternate = findAttributeValue(attributes, 'alternateProtocol');

  if (!protocol) {
    return undefined;
  }

  const primary =
    protocol === 'rest' ? `${transport ? formatProtocolValue(transport) : 'HTTP'}/REST` : formatProtocolValue(protocol);
  return alternate ? `${primary}/${formatProtocolValue(alternate)}` : primary;
}

export function findAttributeValue(attributes: SysmlMember[], name: string) {
  return attributes.find((attribute) => attribute.name === name)?.value;
}

export function formatProtocolValue(value: string) {
  const labels: Record<string, string> = {
    amqp: 'AMQP',
    awsApi: 'AWS API',
    grpc: 'gRPC',
    http: 'HTTP',
    https: 'HTTPS',
    mcp: 'MCP',
    oauth2: 'OAuth2',
    oidc: 'OIDC',
    postgresql: 'PostgreSQL',
    rest: 'REST',
    s3: 'S3',
    websocket: 'WebSocket',
  };

  return labels[value] ?? formatEnumValue(value);
}

export function formatEnumValue(value: string) {
  const labels: Record<string, string> = {
    apiKey: 'API key', // pragma: allowlist secret
    awsIam: 'AWS IAM',
    basicAuth: 'Basic Auth',
    clientSecret: 'Client Secret', // pragma: allowlist secret
    efsSc: 'EFS SC',
    gp3: 'gp3',
    hmac: 'HMAC',
    jwtBearer: 'JWT',
    oidcJwt: 'OIDC/JWT',
    oauth2: 'OAuth2',
    serviceAccount: 'Service Account',
  };

  return labels[value] ?? value.replace(/^"(.*)"$/, '$1');
}
