import type {
  Endpoint,
  ParsedConnection,
  PartDefinition,
  PartInstance,
  SubPart,
  SysmlMember,
  SysmlModel,
  SysmlPort,
  SysmlSystem,
  SysmlView,
} from './types';

type Block = {
  name: string;
  body: string;
  start: number;
  end: number;
};

const identifier = '[A-Za-z_][A-Za-z0-9_]*';

export function parseSysml(text: string): SysmlModel {
  const diagnostics: string[] = [];
  const packageName = matchFirst(text, new RegExp(`\\bpackage\\s+(${identifier})\\s*\\{`));
  const partDefinitions = parsePartDefinitions(text);
  const systems = parseSystems(text, partDefinitions);
  const view = parseView(text);

  if (systems.length === 0) {
    // No explicit `part Suite { … }` block. Some models wire components together at
    // package scope using the part-def names directly (e.g. `connect a to b : Http;`),
    // so synthesize a system that treats each part def as an instance.
    const synthesized = synthesizePackageSystem(text, packageName, partDefinitions);
    if (synthesized) {
      systems.push(synthesized);
    } else {
      diagnostics.push('No system part block with explicit instances and connections was found.');
    }
  }

  const selectedSystem = systems.find((system) => view?.expose?.startsWith(`${system.name}::`)) ??
    systems.reduce<SysmlSystem | undefined>(
      (best, system) => (!best || system.connections.length > best.connections.length ? system : best),
      undefined,
    ) ?? {
      name: 'UnresolvedSystem',
      instances: [],
      connections: [],
    };

  validateModel(selectedSystem, diagnostics);

  return {
    packageName,
    partDefinitions,
    systems,
    selectedSystem,
    view,
    diagnostics,
  };
}

function parsePartDefinitions(text: string): Map<string, PartDefinition> {
  const definitions = new Map<string, PartDefinition>();
  const regex = new RegExp(`\\bpart\\s+def\\s+(${identifier})(?:\\s*:>\\s*${identifier})?\\s*\\{`, 'g');
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text))) {
    const block = readBlock(text, match.index, match[1]);
    if (!block) {
      continue;
    }

    const topLevelBody = stripNestedBlocks(block.body);
    definitions.set(block.name, {
      name: block.name,
      doc: extractLeadingDoc(block.body),
      // Direct ports only: remove nested subpart blocks so their ports do not
      // flatten up onto the owning definition.
      ports: parsePorts(stripSubpartBlocks(block.body)),
      attributes: parseMembers(topLevelBody, 'attribute'),
      actions: parseMembers(topLevelBody, 'action'),
      states: parseMembers(topLevelBody, 'state'),
      parts: parseSubParts(block.body),
    });

    regex.lastIndex = block.end;
  }

  return definitions;
}

function parseSubParts(body: string): SubPart[] {
  const parts: SubPart[] = [];
  // Composition subparts are `part <name> { … }` (a body, no `: Type`); instances
  // are `part <name> : Type;` and are handled separately.
  const regex = new RegExp(`\\bpart\\s+(?!def\\b)(${identifier})\\s*\\{`, 'g');
  let match: RegExpExecArray | null;

  while ((match = regex.exec(body))) {
    const block = readBlock(body, match.index, match[1]);
    if (!block) {
      continue;
    }

    parts.push({
      name: block.name,
      doc: extractLeadingDoc(block.body),
      ports: parsePorts(stripSubpartBlocks(block.body)),
      attributes: parseMembers(stripNestedBlocks(block.body), 'attribute'),
      parts: parseSubParts(block.body),
    });

    regex.lastIndex = block.end;
  }

  return parts;
}

function parseMembers(body: string, keyword: 'attribute' | 'action' | 'state'): SysmlMember[] {
  const members: SysmlMember[] = [];
  const regex = new RegExp(
    `\\b${keyword}\\s+(${identifier})(?:\\s*:\\s*(${identifier}))?(?:\\s*=\\s*([^;{]+))?\\s*(?:;|\\{)`,
    'g',
  );
  let match: RegExpExecArray | null;

  while ((match = regex.exec(body))) {
    members.push({
      name: match[1],
      type: match[2],
      value: match[3]?.trim(),
    });
  }

  return members;
}

function parseSystems(text: string, definitions: Map<string, PartDefinition>): SysmlSystem[] {
  const systems: SysmlSystem[] = [];
  const regex = new RegExp(`\\bpart\\s+(?!def\\b)(${identifier})\\s*\\{`, 'g');
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text))) {
    const block = readBlock(text, match.index, match[1]);
    if (!block) {
      continue;
    }

    const instances = parseInstances(block.body, definitions);
    const connections = parseConnections(block.body);

    if (instances.length > 0 || connections.length > 0) {
      systems.push({
        name: block.name,
        doc: extractLeadingDoc(block.body),
        instances,
        connections,
      });
    }

    regex.lastIndex = block.end;
  }

  return systems;
}

function synthesizePackageSystem(
  text: string,
  packageName: string | undefined,
  definitions: Map<string, PartDefinition>,
): SysmlSystem | undefined {
  const connections = parseConnections(text);
  if (connections.length === 0) {
    return undefined;
  }
  // Every part def is a candidate component; also include any referenced names
  // that are not defined locally (external systems) so connections resolve.
  const names = new Set<string>(definitions.keys());
  for (const connection of connections) {
    names.add(connection.source.instance);
    names.add(connection.target.instance);
  }
  const instances: PartInstance[] = [...names].map((name) => ({
    name,
    type: name,
    definition: definitions.get(name),
  }));
  return { name: packageName ?? 'Package', instances, connections };
}

function parseInstances(body: string, definitions: Map<string, PartDefinition>): PartInstance[] {
  const instances: PartInstance[] = [];
  const regex = new RegExp(`\\bpart\\s+(${identifier})\\s*:\\s*(${identifier})\\s*;`, 'g');
  let match: RegExpExecArray | null;

  while ((match = regex.exec(body))) {
    instances.push({
      name: match[1],
      type: match[2],
      definition: definitions.get(match[2]),
    });
  }

  return instances;
}

function parseConnections(body: string): ParsedConnection[] {
  const connections: ParsedConnection[] = [];
  // Accepts dotted paths of any depth on each side plus an optional port-type
  // annotation, covering all three real forms:
  //   connect a.port to b.port;                     (flat)
  //   connect a.sub.port to b.sub.port;             (nested)
  //   connect a to b : HttpPort;                    (typed, no ports)
  // Anchoring on `connect` also matches the `connection <name> connect …` form.
  const path = `${identifier}(?:\\.${identifier})*`;
  const regex = new RegExp(`\\bconnect\\s+(${path})\\s+to\\s+(${path})(?:\\s*:\\s*(${identifier}))?\\s*;`, 'g');
  let match: RegExpExecArray | null;

  while ((match = regex.exec(body))) {
    const [, sourcePath, targetPath, portType] = match;
    const source = parseEndpoint(sourcePath, portType);
    const target = parseEndpoint(targetPath, portType);
    connections.push({
      id: `${endpointKey(source)}->${endpointKey(target)}-${connections.length}`,
      source,
      target,
    });
  }

  return connections;
}

function parseEndpoint(pathExpression: string, portType?: string): Endpoint {
  const segments = pathExpression.split('.');
  const instance = segments[0];
  if (segments.length === 1) {
    return { instance, path: [], port: undefined, portType };
  }
  return {
    instance,
    path: segments.slice(1, -1),
    port: segments[segments.length - 1],
    portType,
  };
}

function endpointKey(endpoint: Endpoint): string {
  return [endpoint.instance, ...endpoint.path, endpoint.port].filter(Boolean).join('.');
}

function parsePorts(body: string): SysmlPort[] {
  const ports: SysmlPort[] = [];
  const regex = new RegExp(`\\bport\\s+(${identifier})(?:\\s*:\\s*(${identifier}))?\\s*(\\{|;)`, 'g');
  let match: RegExpExecArray | null;

  while ((match = regex.exec(body))) {
    const name = match[1];
    const type = match[2];
    const token = match[3];

    if (token === ';') {
      ports.push({ name, type, attributes: [] });
      continue;
    }

    const openBrace = body.indexOf('{', match.index);
    const closeBrace = findMatchingBrace(body, openBrace);
    if (closeBrace === -1) {
      ports.push({ name, attributes: [] });
      continue;
    }

    const portBody = body.slice(openBrace + 1, closeBrace);
    const direction = matchFirst(portBody, /\bdirection\s*=\s*(in|out|both)\s*;/) as SysmlPort['direction'];
    ports.push({
      name,
      type,
      direction,
      doc: extractLeadingDoc(portBody),
      attributes: parseMembers(stripNestedBlocks(portBody), 'attribute'),
    });

    regex.lastIndex = closeBrace + 1;
  }

  return ports;
}

function parseView(text: string): SysmlView | undefined {
  const viewMatch = text.match(/\bview\s+'([^']+)'\s*:\s*([A-Za-z0-9_:]+)\s*\{/);
  if (!viewMatch) {
    return undefined;
  }

  const block = readBlock(text, viewMatch.index ?? 0, viewMatch[1]);
  const expose = block ? matchFirst(block.body, /\bexpose\s+([^;]+)\s*;/) : undefined;

  return {
    name: viewMatch[1],
    type: viewMatch[2],
    expose,
  };
}

function validateModel(system: SysmlSystem, diagnostics: string[]) {
  const instances = new Map(system.instances.map((instance) => [instance.name, instance]));

  for (const instance of system.instances) {
    if (!instance.definition) {
      diagnostics.push(`${instance.name} references missing part definition ${instance.type}.`);
    }
  }

  for (const connection of system.connections) {
    validateEndpoint(connection.id, 'source', connection.source, instances, diagnostics);
    validateEndpoint(connection.id, 'target', connection.target, instances, diagnostics);
  }
}

function validateEndpoint(
  connectionId: string,
  role: 'source' | 'target',
  endpoint: Endpoint,
  instances: Map<string, PartInstance>,
  diagnostics: string[],
) {
  const instance = instances.get(endpoint.instance);
  if (!instance) {
    diagnostics.push(`${connectionId} references missing ${role} part ${endpoint.instance}.`);
    return;
  }
  // Port-less typed connections carry no port to validate.
  if (!endpoint.port || !instance.definition) {
    return;
  }
  if (!endpointPortExists(instance.definition, endpoint)) {
    const address = [endpoint.instance, ...endpoint.path, endpoint.port].join('.');
    diagnostics.push(`${address} is not declared on ${instance.type}.`);
  }
}

// Resolves an endpoint's port through its subpart path; missing subparts make it
// unresolvable (treated as "not declared").
function endpointPortExists(definition: PartDefinition, endpoint: Endpoint): boolean {
  let ports = definition.ports;
  let subparts = definition.parts;
  for (const segment of endpoint.path) {
    const next = subparts.find((part) => part.name === segment);
    if (!next) {
      return false;
    }
    ports = next.ports;
    subparts = next.parts;
  }
  return ports.some((port) => port.name === endpoint.port);
}

function readBlock(text: string, start: number, name: string): Block | undefined {
  const openBrace = text.indexOf('{', start);
  if (openBrace === -1) {
    return undefined;
  }

  const closeBrace = findMatchingBrace(text, openBrace);
  if (closeBrace === -1) {
    return undefined;
  }

  return {
    name,
    body: text.slice(openBrace + 1, closeBrace),
    start,
    end: closeBrace + 1,
  };
}

// Blanks out nested composition subpart blocks (`part <name> { … }`) while leaving
// everything else — including `port … { }` blocks — intact, so a definition's
// *direct* ports can be parsed without flattening its subparts' ports up.
function stripSubpartBlocks(body: string): string {
  const regex = new RegExp(`\\bpart\\s+(?!def\\b)(${identifier})\\s*\\{`, 'g');
  const ranges: Array<[number, number]> = [];
  let match: RegExpExecArray | null;

  while ((match = regex.exec(body))) {
    const block = readBlock(body, match.index, match[1]);
    if (!block) {
      continue;
    }
    ranges.push([match.index, block.end]);
    regex.lastIndex = block.end;
  }

  let result = body;
  for (const [start, end] of ranges) {
    result = result.slice(0, start) + ' '.repeat(end - start) + result.slice(end);
  }
  return result;
}

function stripNestedBlocks(body: string): string {
  let depth = 0;
  let stripped = '';

  for (const char of body) {
    if (char === '{') {
      depth += 1;
      stripped += ' ';
      continue;
    }

    if (char === '}') {
      depth = Math.max(0, depth - 1);
      stripped += ' ';
      continue;
    }

    stripped += depth === 0 ? char : ' ';
  }

  return stripped;
}

function findMatchingBrace(text: string, openBrace: number): number {
  let depth = 0;

  for (let index = openBrace; index < text.length; index += 1) {
    const char = text[index];
    if (char === '{') {
      depth += 1;
    } else if (char === '}') {
      depth -= 1;
      if (depth === 0) {
        return index;
      }
    }
  }

  return -1;
}

function extractLeadingDoc(body: string): string | undefined {
  // Documentation must lead the block, but accept either textual form the model
  // emits: the comment form `doc /* … */` and the string form `doc "…"`. Both
  // tolerate an optional identifier before the body (e.g. `doc Purpose /* … */`).
  const commentForm = body.match(/^\s*doc\b[^/"]*\/\*([\s\S]*?)\*\//);
  if (commentForm) {
    return normalizeDoc(commentForm[1]);
  }
  const stringForm = body.match(/^\s*doc\b[^"]*"((?:[^"\\]|\\.)*)"/);
  if (stringForm) {
    return normalizeDoc(stringForm[1]);
  }
  return undefined;
}

function normalizeDoc(doc: string): string {
  return doc
    .replace(/\s+/g, ' ')
    .replace(/\s+([,.;:])/g, '$1')
    .trim();
}

function matchFirst(text: string, regex: RegExp): string | undefined {
  return text.match(regex)?.[1];
}
