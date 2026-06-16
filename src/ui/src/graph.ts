import type { Edge, Node } from '@xyflow/react';
import type {
  DiagramEdgeData,
  DiagramNodeData,
  EdgeAggregate,
  EdgeFilter,
  Endpoint,
  NodeCompartmentKey,
  NodeCompartmentState,
  NodePosition,
  NodeSize,
  ParsedConnection,
  PartDefinition,
  PartInstance,
  PortPlacement,
  PortDirection,
  SubPart,
  SysmlConnection,
  SysmlMember,
  SysmlModel,
  SysmlPort,
} from './types';
import { classifyInstance } from './contextClassifier';
import { getNodeSize } from './layout';
import { sourcePortHandleId, targetPortHandleId } from './portHandles';
import { normalizePortPlacements, placePorts } from './portPlacement';

// Shared per-render inputs both projections need.
export type BuildContext = {
  query: string;
  filter: EdgeFilter;
  selectedNodeId?: string;
  selectedPort?: { nodeId: string; portName: string };
  placementOverrides: Record<string, Record<string, PortPlacement>>;
  lockedPlacementOverrides: Record<string, Record<string, boolean>>;
  positionOverrides: Record<string, NodePosition>;
  compartmentDefaultOpen: boolean;
  compartmentOverrides: Record<string, Partial<NodeCompartmentState>>;
  onPortMove?: (instanceName: string, portName: string, placement: PortPlacement) => void;
  onPortSelect?: (instanceName: string, portName: string) => void;
  onCompartmentToggle?: (instanceName: string, compartment: NodeCompartmentKey, open: boolean) => void;
};

type FlatSystem = {
  name: string;
  instances: PartInstance[];
  connections: SysmlConnection[];
};

type DiagramElements = {
  nodes: Array<Node<DiagramNodeData>>;
  edges: Array<Edge<DiagramEdgeData>>;
};

// ---------------------------------------------------------------------------
// Top-level overview: instance nodes + aggregated instance-pair edges.
// ---------------------------------------------------------------------------

export function buildTopLevelElements(model: SysmlModel, context: BuildContext): DiagramElements {
  const system = model.selectedSystem;
  // The overview now renders ports on the border and attaches edges to the real
  // port handles, exactly like the excerpt view. Lower each cross-instance
  // connection to an instance-level port, augment instances so every referenced
  // port has an anchor, then reuse the shared per-port builder.
  const connections = lowerToInstancePorts(system.connections);
  const instances = augmentInstancesWithReferencedPorts(system.instances, connections);
  const flat: FlatSystem = { name: system.name, instances, connections };

  const { nodes, edges } = buildDiagramElementsFromSystem(flat, context);
  const hasSubpartsByName = new Map(
    system.instances.map((instance) => [instance.name, (instance.definition?.parts.length ?? 0) > 0]),
  );

  return {
    nodes: nodes.map((node) => ({
      ...node,
      data: { ...node.data, overview: true, hasSubparts: hasSubpartsByName.get(node.id) ?? false },
    })),
    edges,
  };
}

// Lower a fully-addressed connection onto the two instances it spans, choosing an
// instance-level port name to attach to: a directly-named top-level port when the
// endpoint has one, otherwise the first subpart segment (so nested wiring still
// lands on a stable border anchor). Intra-instance wiring belongs to the excerpt.
function lowerToInstancePorts(connections: ParsedConnection[]): SysmlConnection[] {
  const lowered: SysmlConnection[] = [];
  for (const connection of connections) {
    if (connection.source.instance === connection.target.instance) {
      continue;
    }
    lowered.push({
      id: connection.id,
      sourcePart: connection.source.instance,
      sourcePort: instancePortName(connection.source),
      targetPart: connection.target.instance,
      targetPort: instancePortName(connection.target),
    });
  }
  return lowered;
}

function instancePortName(endpoint: Endpoint): string {
  return endpoint.path[0] ?? endpoint.port ?? endpoint.instance;
}

// Ensure every instance-level port referenced by a lowered connection exists on
// the instance definition, synthesizing bare ports for ones the model didn't
// declare so their edges have a real handle to attach to.
function augmentInstancesWithReferencedPorts(
  instances: PartInstance[],
  connections: SysmlConnection[],
): PartInstance[] {
  const referenced = new Map<string, Set<string>>();
  const note = (part: string, port: string) => {
    const set = referenced.get(part) ?? new Set<string>();
    set.add(port);
    referenced.set(part, set);
  };
  for (const connection of connections) {
    note(connection.sourcePart, connection.sourcePort);
    note(connection.targetPart, connection.targetPort);
  }

  return instances.map((instance) => {
    const ports = instance.definition?.ports ?? [];
    const existing = new Set(ports.map((port) => port.name));
    const inferredDirections = inferPortDirections(instance.name, connections);
    const directedPorts = applyInferredDirections(ports, inferredDirections);
    const synthetic: SysmlPort[] = [...(referenced.get(instance.name) ?? [])]
      .filter((name) => !existing.has(name))
      .map((name) => ({ name, direction: inferredDirections.get(name), attributes: [] }));
    if (synthetic.length === 0 && directedPorts.every((port, index) => port === ports[index])) {
      return instance;
    }

    const base = instance.definition;
    const definition: PartDefinition = base
      ? { ...base, ports: [...directedPorts, ...synthetic] }
      : { name: instance.type, ports: synthetic, attributes: [], actions: [], states: [], parts: [] };
    return { ...instance, definition };
  });
}

function inferPortDirections(instanceName: string, connections: SysmlConnection[]): Map<string, PortDirection> {
  const stats = new Map<string, { source: number; target: number }>();
  const note = (port: string, side: 'source' | 'target') => {
    const current = stats.get(port) ?? { source: 0, target: 0 };
    current[side] += 1;
    stats.set(port, current);
  };

  for (const connection of connections) {
    if (connection.sourcePart === instanceName) {
      note(connection.sourcePort, 'source');
    }
    if (connection.targetPart === instanceName) {
      note(connection.targetPort, 'target');
    }
  }

  const directions = new Map<string, PortDirection>();
  for (const [port, stat] of stats) {
    if (stat.source > 0 && stat.target > 0) {
      directions.set(port, 'both');
    } else if (stat.source > 0) {
      directions.set(port, 'out');
    } else {
      directions.set(port, 'in');
    }
  }
  return directions;
}

function aggregateConnections(model: SysmlModel, connections: ParsedConnection[]): EdgeAggregate[] {
  const byPair = new Map<string, EdgeAggregate>();

  for (const connection of connections) {
    const source = connection.source.instance;
    const target = connection.target.instance;
    // Intra-instance connections belong to the excerpt view, not the overview.
    if (source === target) {
      continue;
    }
    const key = `${source}->${target}`;
    let aggregate = byPair.get(key);
    if (!aggregate) {
      aggregate = { sourceInstance: source, targetInstance: target, count: 0, portTypes: [], pairs: [] };
      byPair.set(key, aggregate);
    }
    aggregate.count += 1;
    const portType = resolvePortType(model, connection.source) ?? resolvePortType(model, connection.target);
    if (portType && !aggregate.portTypes.includes(portType)) {
      aggregate.portTypes.push(portType);
    }
    aggregate.pairs.push(`${endpointLabel(connection.source)} → ${endpointLabel(connection.target)}`);
  }

  return [...byPair.values()];
}

function resolvePortType(model: SysmlModel, endpoint: Endpoint): string | undefined {
  if (endpoint.portType) {
    return endpoint.portType;
  }
  const instance = model.selectedSystem.instances.find((candidate) => candidate.name === endpoint.instance);
  if (!instance?.definition || !endpoint.port) {
    return undefined;
  }
  let ports = instance.definition.ports;
  let parts = instance.definition.parts;
  for (const segment of endpoint.path) {
    const next = parts.find((part) => part.name === segment);
    if (!next) {
      return undefined;
    }
    ports = next.ports;
    parts = next.parts;
  }
  return ports.find((port) => port.name === endpoint.port)?.type;
}

function filterAggregates(aggregates: EdgeAggregate[], context: BuildContext): EdgeAggregate[] {
  if (context.filter === 'selected') {
    return context.selectedNodeId
      ? aggregates.filter(
          (aggregate) =>
            aggregate.sourceInstance === context.selectedNodeId || aggregate.targetInstance === context.selectedNodeId,
        )
      : aggregates;
  }
  if (context.filter === 'ingress') {
    return aggregates.filter((aggregate) => aggregateMatchesTerms(aggregate, ingressTerms));
  }
  if (context.filter === 'data') {
    return aggregates.filter((aggregate) => aggregateMatchesTerms(aggregate, dataTerms));
  }
  if (context.filter === 'external') {
    return aggregates.filter((aggregate) => aggregateMatchesTerms(aggregate, externalTerms));
  }
  return aggregates;
}

function countAggregateDegree(aggregates: EdgeAggregate[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const aggregate of aggregates) {
    counts.set(aggregate.sourceInstance, (counts.get(aggregate.sourceInstance) ?? 0) + aggregate.count);
    counts.set(aggregate.targetInstance, (counts.get(aggregate.targetInstance) ?? 0) + aggregate.count);
  }
  return counts;
}

function aggregateId(aggregate: EdgeAggregate): string {
  return `agg-${aggregate.sourceInstance}->${aggregate.targetInstance}`;
}

function aggregateLabel(aggregate: EdgeAggregate): string {
  const types = aggregate.portTypes.map(shortPortType).filter(Boolean).slice(0, 3);
  const countLabel = aggregate.count > 1 ? `×${aggregate.count}` : '';
  const typeLabel = types.join(', ');
  return [countLabel, typeLabel].filter(Boolean).join('  ');
}

function shortPortType(type: string): string {
  return type.replace(/Port$/, '');
}

function aggregateText(aggregate: EdgeAggregate): string {
  return `${aggregate.sourceInstance} ${aggregate.targetInstance} ${aggregate.pairs.join(' ')} ${aggregate.portTypes.join(' ')}`.toLowerCase();
}

function aggregateMatchesSearch(aggregate: EdgeAggregate, normalizedQuery: string): boolean {
  if (!normalizedQuery) {
    return false;
  }
  return aggregateText(aggregate).includes(normalizedQuery);
}

function aggregateMatchesTerms(aggregate: EdgeAggregate, terms: string[]): boolean {
  const haystack = aggregateText(aggregate);
  return terms.some((term) => haystack.includes(term));
}

function endpointLabel(endpoint: Endpoint): string {
  return [endpoint.instance, ...endpoint.path, endpoint.port].filter(Boolean).join('.');
}

// ---------------------------------------------------------------------------
// Excerpt: subparts of one focused instance + collapsed external boundaries.
// ---------------------------------------------------------------------------

export function buildExcerptElements(model: SysmlModel, focusPath: string[], context: BuildContext): DiagramElements {
  const { system, boundaryNames } = deriveExcerptSystem(model, focusPath);
  return buildDiagramElementsFromSystem(system, context, boundaryNames);
}

function deriveExcerptSystem(
  model: SysmlModel,
  focusPath: string[],
): { system: FlatSystem; boundaryNames: Set<string> } {
  const focus = resolveFocus(model, focusPath);
  const focusName = focus?.name ?? focusPath[focusPath.length - 1] ?? model.selectedSystem.name;
  const rootName = focusPath[0] ?? focusName;
  const focusSubpath = focusPath.slice(1);
  const subparts = focus?.parts ?? [];
  const boundaryNames = new Set<string>();
  const partsByName = new Map<string, PartInstance>();

  // Seed local nodes: one per subpart, or the focus itself when it has no subparts.
  for (const subpart of subparts) {
    partsByName.set(subpart.name, subpartToInstance(subpart));
  }
  if (subparts.length === 0 && focus) {
    partsByName.set(focus.name, focusToInstance(focus));
  }

  const ensurePart = (name: string): PartInstance => {
    let instance = partsByName.get(name);
    if (!instance) {
      instance = { name, type: name, definition: emptyDefinition(name) };
      partsByName.set(name, instance);
    }
    return instance;
  };
  const ensurePort = (instance: PartInstance, portName: string, portType?: string): void => {
    const ports = instance.definition?.ports;
    if (ports && !ports.some((port) => port.name === portName)) {
      ports.push({ name: portName, type: portType, attributes: [] });
    }
  };

  const connections: SysmlConnection[] = [];
  for (const connection of model.selectedSystem.connections) {
    const sourceInFocus = endpointIsInsideFocus(connection.source, rootName, focusSubpath);
    const targetInFocus = endpointIsInsideFocus(connection.target, rootName, focusSubpath);
    if (!sourceInFocus && !targetInFocus) {
      continue;
    }
    const source = lowerEndpoint(connection.source, rootName, focusSubpath, focusName, subparts.length > 0);
    const target = lowerEndpoint(connection.target, rootName, focusSubpath, focusName, subparts.length > 0);

    ensurePort(ensurePart(source.part), source.port, connection.source.portType);
    ensurePort(ensurePart(target.part), target.port, connection.target.portType);
    if (!sourceInFocus) {
      boundaryNames.add(source.part);
    }
    if (!targetInFocus) {
      boundaryNames.add(target.part);
    }

    connections.push({
      id: connection.id,
      sourcePart: source.part,
      sourcePort: source.port,
      targetPart: target.part,
      targetPort: target.port,
    });
  }

  return {
    system: {
      name: focusName,
      instances: applyInferredDirectionsToInstances([...partsByName.values()], connections),
      connections,
    },
    boundaryNames,
  };
}

// Lower a fully-addressed endpoint to a local node + port within the excerpt.
// Focus endpoints resolve to their first subpart (or the focus shell); external
// endpoints collapse to a boundary node named for the other instance.
function lowerEndpoint(
  endpoint: Endpoint,
  rootName: string,
  focusSubpath: string[],
  focusName: string,
  focusHasSubparts: boolean,
): { part: string; port: string } {
  if (endpointIsInsideFocus(endpoint, rootName, focusSubpath)) {
    const remainder = endpoint.path.slice(focusSubpath.length);
    const part = focusHasSubparts && remainder.length > 0 ? remainder[0] : focusName;
    return { part, port: endpoint.port ?? portFallback(endpoint) };
  }
  const { part: boundaryPart, remainingPath } = boundaryEndpoint(endpoint, rootName, focusSubpath);
  const qualified = [...remainingPath, endpoint.port].filter(Boolean).join('.');
  return { part: boundaryPart, port: qualified || portFallback(endpoint) };
}

function portFallback(endpoint: Endpoint): string {
  return endpoint.portType ?? 'link';
}

function subpartToInstance(subpart: SubPart): PartInstance {
  return {
    name: subpart.name,
    type: subpart.name,
    definition: {
      name: subpart.name,
      doc: subpart.doc,
      ports: [...subpart.ports],
      attributes: subpart.attributes,
      actions: [],
      states: [],
      parts: subpart.parts,
    },
  };
}

type FocusNode = {
  name: string;
  doc?: string;
  ports: SysmlPort[];
  attributes: SysmlMember[];
  parts: SubPart[];
};

function resolveFocus(model: SysmlModel, focusPath: string[]): FocusNode | undefined {
  const root = model.selectedSystem.instances.find((instance) => instance.name === focusPath[0]);
  if (!root) {
    return undefined;
  }
  let focus: FocusNode | undefined = {
    name: root.name,
    doc: root.definition?.doc,
    ports: root.definition?.ports ?? [],
    attributes: root.definition?.attributes ?? [],
    parts: root.definition?.parts ?? [],
  };
  for (const segment of focusPath.slice(1)) {
    const child: SubPart | undefined = focus?.parts.find((part) => part.name === segment);
    focus = child
      ? {
          name: child.name,
          doc: child.doc,
          ports: child.ports,
          attributes: child.attributes,
          parts: child.parts,
        }
      : undefined;
  }
  return focus;
}

function endpointIsInsideFocus(endpoint: Endpoint, rootName: string, focusSubpath: string[]): boolean {
  return endpoint.instance === rootName && focusSubpath.every((segment, index) => endpoint.path[index] === segment);
}

function boundaryEndpoint(
  endpoint: Endpoint,
  rootName: string,
  focusSubpath: string[],
): { part: string; remainingPath: string[] } {
  if (endpoint.instance !== rootName) {
    return { part: endpoint.instance, remainingPath: endpoint.path };
  }
  for (let index = 0; index < focusSubpath.length; index += 1) {
    if (endpoint.path[index] !== focusSubpath[index]) {
      return {
        part: endpoint.path[index] ?? endpoint.instance,
        remainingPath: endpoint.path.slice(index + 1),
      };
    }
  }
  return {
    part: endpoint.path[focusSubpath.length] ?? endpoint.instance,
    remainingPath: endpoint.path.slice(focusSubpath.length + 1),
  };
}

function focusToInstance(focus: FocusNode): PartInstance {
  return {
    name: focus.name,
    type: focus.name,
    definition: {
      name: focus.name,
      doc: focus.doc,
      ports: [...focus.ports],
      attributes: focus.attributes,
      actions: [],
      states: [],
      parts: focus.parts,
    },
  };
}

function emptyDefinition(name: string): PartDefinition {
  return { name, ports: [], attributes: [], actions: [], states: [], parts: [] };
}

function applyInferredDirectionsToInstances(instances: PartInstance[], connections: SysmlConnection[]): PartInstance[] {
  return instances.map((instance) => {
    const definition = instance.definition;
    if (!definition) {
      return instance;
    }
    const inferredDirections = inferPortDirections(instance.name, connections);
    const ports = applyInferredDirections(definition.ports, inferredDirections);
    if (ports.every((port, index) => port === definition.ports[index])) {
      return instance;
    }
    return { ...instance, definition: { ...definition, ports } };
  });
}

function applyInferredDirections(ports: SysmlPort[], inferredDirections: Map<string, PortDirection>): SysmlPort[] {
  return ports.map((port) => (port.direction ? port : { ...port, direction: inferredDirections.get(port.name) }));
}

// ---------------------------------------------------------------------------
// Shared flat pipeline (used by the excerpt projection).
// ---------------------------------------------------------------------------

function buildDiagramElementsFromSystem(
  system: FlatSystem,
  context: BuildContext,
  boundaryNames: Set<string> = new Set(),
): DiagramElements {
  const normalizedQuery = context.query.trim().toLowerCase();
  const visibleConnections = filterConnections(system, context);
  const connectedCounts = countConnections(system.connections);
  const visibleEdgeIds = new Set(visibleConnections.map((connection) => connection.id));
  const placementsByInstance = new Map(
    system.instances.map((instance) => [
      instance.name,
      normalizePortPlacements({
        ...placePorts(instance, system.connections),
        ...(context.placementOverrides[instance.name] ?? {}),
      }),
    ]),
  );

  const nodes = system.instances.map<Node<DiagramNodeData>>((instance, index) => {
    const isBoundary = boundaryNames.has(instance.name);
    const portPlacements = placementsByInstance.get(instance.name) ?? {};
    const lockedPortPlacements = context.lockedPlacementOverrides[instance.name] ?? {};
    const matchesSearch = matchesNode(instance, normalizedQuery);
    const highlighted =
      matchesSearch || instance.name === context.selectedNodeId || instance.name === context.selectedPort?.nodeId;
    const connectedToVisibleEdge = visibleConnections.some(
      (connection) => connection.sourcePart === instance.name || connection.targetPart === instance.name,
    );
    const dimmed =
      (normalizedQuery.length > 0 && !matchesSearch) ||
      (context.filter === 'selected' &&
        Boolean(context.selectedNodeId || context.selectedPort) &&
        instance.name !== context.selectedNodeId &&
        instance.name !== context.selectedPort?.nodeId &&
        !connectedToVisibleEdge);

    return {
      id: instance.name,
      type: 'sysmlPart',
      position: context.positionOverrides[instance.name] ?? gridPosition(index, getNodeSize(instance)),
      data: {
        instance,
        context: classifyInstance(instance),
        portRelations: getPortRelations(instance.name, system.connections),
        portPlacements,
        lockedPortPlacements,
        onPortMove: isBoundary ? undefined : context.onPortMove,
        onPortSelect: isBoundary ? undefined : context.onPortSelect,
        compartmentState: compartmentState(instance.name, context),
        onCompartmentToggle: context.onCompartmentToggle,
        selectedPortName: context.selectedPort?.nodeId === instance.name ? context.selectedPort.portName : undefined,
        connectedCount: connectedCounts.get(instance.name) ?? 0,
        highlighted,
        dimmed,
        boundary: isBoundary,
      },
      className: nodeClassName(highlighted, dimmed),
    };
  });

  const edges = system.connections
    .filter((connection) => visibleEdgeIds.has(connection.id))
    .map<Edge<DiagramEdgeData>>((connection) => {
      const attachedToSelected = context.selectedPort
        ? isConnectionAttachedToPort(connection, context.selectedPort)
        : Boolean(context.selectedNodeId) &&
          (connection.sourcePart === context.selectedNodeId || connection.targetPart === context.selectedNodeId);
      const matchesSearch = matchesConnection(connection, normalizedQuery);
      const highlighted = matchesSearch || attachedToSelected;
      const dimmed =
        (normalizedQuery.length > 0 && !matchesSearch) ||
        (Boolean(context.selectedNodeId || context.selectedPort) && !attachedToSelected);

      return {
        id: connection.id,
        source: connection.sourcePart,
        sourceHandle: sourcePortHandleId(
          connection.sourcePort,
          placementsByInstance.get(connection.sourcePart)?.[connection.sourcePort]?.side ?? 'right',
        ),
        target: connection.targetPart,
        targetHandle: targetPortHandleId(
          connection.targetPort,
          placementsByInstance.get(connection.targetPart)?.[connection.targetPort]?.side ?? 'left',
        ),
        type: 'smoothstep',
        interactionWidth: 24,
        animated: highlighted,
        data: { connection, highlighted, dimmed },
        className: nodeClassName(highlighted, dimmed),
      };
    });

  return { nodes, edges };
}

function compartmentState(instanceName: string, context: BuildContext): NodeCompartmentState {
  const overrides = context.compartmentOverrides[instanceName] ?? {};
  return {
    attributes: overrides.attributes ?? context.compartmentDefaultOpen,
    ports: overrides.ports ?? context.compartmentDefaultOpen,
  };
}

function gridPosition(index: number, size: NodeSize): NodePosition {
  return {
    x: (index % 6) * (size.width + 120),
    y: Math.floor(index / 6) * (size.height + 120),
  };
}

function nodeClassName(highlighted: boolean, dimmed: boolean): string {
  return [highlighted ? 'is-highlighted' : '', dimmed ? 'is-dimmed' : ''].filter(Boolean).join(' ');
}

function getPortRelations(instanceName: string, connections: SysmlConnection[]): Record<string, string[]> {
  const relations: Record<string, string[]> = {};

  for (const connection of connections) {
    if (connection.sourcePart === instanceName) {
      relations[connection.sourcePort] = [
        ...(relations[connection.sourcePort] ?? []),
        `${connection.sourcePort}:${connection.targetPart}.${connection.targetPort}`,
      ];
    }
    if (connection.targetPart === instanceName) {
      relations[connection.targetPort] = [
        ...(relations[connection.targetPort] ?? []),
        `${connection.sourcePart}.${connection.sourcePort}:${connection.targetPort}`,
      ];
    }
  }

  return relations;
}

function filterConnections(system: FlatSystem, context: BuildContext): SysmlConnection[] {
  if (context.filter === 'selected') {
    if (context.selectedPort) {
      return system.connections.filter((connection) => isConnectionAttachedToPort(connection, context.selectedPort!));
    }
    return context.selectedNodeId
      ? system.connections.filter(
          (connection) =>
            connection.sourcePart === context.selectedNodeId || connection.targetPart === context.selectedNodeId,
        )
      : system.connections;
  }

  if (context.filter === 'ingress') {
    return system.connections.filter((connection) => matchesTerms(connection, ingressTerms));
  }
  if (context.filter === 'data') {
    return system.connections.filter((connection) => matchesTerms(connection, dataTerms));
  }
  if (context.filter === 'external') {
    return system.connections.filter((connection) => matchesTerms(connection, externalTerms));
  }

  return system.connections;
}

function isConnectionAttachedToPort(connection: SysmlConnection, selectedPort: { nodeId: string; portName: string }) {
  return (
    (connection.sourcePart === selectedPort.nodeId && connection.sourcePort === selectedPort.portName) ||
    (connection.targetPart === selectedPort.nodeId && connection.targetPort === selectedPort.portName)
  );
}

function countConnections(connections: SysmlConnection[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const connection of connections) {
    counts.set(connection.sourcePart, (counts.get(connection.sourcePart) ?? 0) + 1);
    counts.set(connection.targetPart, (counts.get(connection.targetPart) ?? 0) + 1);
  }
  return counts;
}

function matchesNode(instance: PartInstance, normalizedQuery: string): boolean {
  if (!normalizedQuery) {
    return false;
  }
  const ports = instance.definition?.ports ?? [];
  const haystack = [instance.name, instance.type, ...ports.flatMap((port) => [port.name, port.doc ?? ''])]
    .join(' ')
    .toLowerCase();
  return haystack.includes(normalizedQuery);
}

function matchesConnection(connection: SysmlConnection, normalizedQuery: string): boolean {
  if (!normalizedQuery) {
    return false;
  }
  return connectionText(connection).toLowerCase().includes(normalizedQuery);
}

function matchesTerms(connection: SysmlConnection, terms: string[]): boolean {
  const haystack = connectionText(connection).toLowerCase();
  return terms.some((term) => haystack.includes(term));
}

function connectionText(connection: SysmlConnection): string {
  return `${connection.sourcePart} ${connection.sourcePort} ${connection.targetPart} ${connection.targetPort}`;
}

const ingressTerms = ['users', 'route53', 'alb', 'ingress', 'https', 'http', 'dns', 'certificate', 'internet'];

const dataTerms = [
  'postgres',
  'database',
  'db',
  'storage',
  'volume',
  'milvus',
  'opensearch',
  'efs',
  'ebs',
  's3',
  'backup',
];

const externalTerms = [
  'openai',
  'azure',
  'source',
  'embedding',
  'reranker',
  'external',
  'internet',
  'searxng',
  'crawl',
];
